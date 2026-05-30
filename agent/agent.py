import asyncio
import inspect
import json
import logging
import os
from typing import Annotated, Optional

import aiohttp
from livekit.agents import Agent, AgentSession, AutoSubscribe, JobContext, RunContext, WorkerOptions, cli, function_tool
from livekit.plugins import google

logger = logging.getLogger("civix-agente")

LARAVEL_BACKEND_URL = os.getenv("LARAVEL_BACKEND_URL", "https://alerta.civix.pe")


class CivixAgent(Agent):
    def __init__(
        self,
        distrito: Optional[str],
        tenant_id: str,
        llamada_token: Optional[str],
        room,
        cobertura_detectada: bool,
        tenants_disponibles: list[str],
    ):
        self._distrito = (distrito or "").strip()
        self._tenant_id = tenant_id
        self._llamada_token = llamada_token
        self._room = room
        self._cobertura_detectada = cobertura_detectada
        self._tenants_disponibles = [t for t in tenants_disponibles if t]
        self._transfer_done = False
        self._closed = False
        self._silence_task: Optional[asyncio.Task] = None
        self._silence_prompts = 0
        self._invalid_district_attempts = 0
        self._silence_timeout_seconds = 4
        self._directory_shutdown_delay = 22

        destino_detectado = self._distrito if self._cobertura_detectada and self._distrito else ""
        if destino_detectado:
            first_prompt = (
                f"Hola soy Civix y te estoy comunicando con la central de serenazgo de {destino_detectado}. Mantente en línea, por favor."
            )
            routing_rule = (
                f"El distrito ya fue confirmado antes de iniciar la llamada. No preguntes si desea cambiar de distrito. "
                f"El sistema transferirá automáticamente a '{destino_detectado}'."
            )
        else:
            first_prompt = (
                "No detecté tu distrito. ¿A qué central de serenazgo deseas comunicarte?"
            )
            routing_rule = "Llama `transferir_llamada` con el distrito que diga el ciudadano."

        instructions = (
            "Eres Civix, la inteligencia artificial de la central de Serenazgo. "
            f"Tu primer mensaje absoluto debe ser: '{first_prompt}'. "
            f"{routing_rule} "
            "Si el backend devuelve telefonos de referencia, dicta los numeros lentamente y avisa que no puedes transferir a ese distrito por ahora. "
            "Si el backend indica distrito no disponible sin telefonos, dilo brevemente y pregunta si desea otra central. "
            "Si se tuvo que preguntar por distrito y el ciudadano no responde, se le repreguntará una sola vez y luego el sistema cerrará la llamada. "
            "Responde siempre directo al grano, muy corto y con ritmo rápido. "
            "Después de usar `transferir_llamada`, no sigas conversando ni agregues instrucciones nuevas."
        )

        super().__init__(instructions=instructions)

    async def on_enter(self) -> None:
        logger.info("Agente activado, enviando saludo inicial...")
        if self._cobertura_detectada and self._distrito:
            await self.session.generate_reply(
                instructions=(
                    "Di exactamente: "
                    f"'Te estoy comunicando con la central de serenazgo de {self._distrito}. "
                    "Mantente en línea, por favor.'"
                )
            )
            resultado = await self._transferir_llamada_impl(self._distrito, armar_silencio_en_error=False)
            if not self._transfer_done and resultado:
                await self.session.generate_reply(instructions=f"Di exactamente: '{resultado}'")
                if not self._closed:
                    self._arm_silence_timer()
            return

        await self.session.generate_reply(
            instructions="Di INMEDIATAMENTE tu primer mensaje exacto y pregunta a qué central de serenazgo desea comunicarse."
        )
        self._arm_silence_timer()

    async def on_exit(self) -> None:
        self._cancel_silence_timer()

    async def on_user_turn_completed(self, *args, **kwargs) -> None:
        self._cancel_silence_timer()
        if not self._transfer_done and not self._closed:
            self._arm_silence_timer()

    def _format_districts(self) -> str:
        if not self._tenants_disponibles:
            return "ninguno"
        if len(self._tenants_disponibles) == 1:
            return self._tenants_disponibles[0]
        return ", ".join(self._tenants_disponibles[:-1]) + " o " + self._tenants_disponibles[-1]

    def _arm_silence_timer(self) -> None:
        self._cancel_silence_timer()
        self._silence_task = asyncio.create_task(self._handle_silence_timeout())

    def _cancel_silence_timer(self) -> None:
        current = asyncio.current_task()
        if self._silence_task and not self._silence_task.done() and self._silence_task is not current:
            self._silence_task.cancel()
        self._silence_task = None

    async def _handle_silence_timeout(self) -> None:
        try:
            await asyncio.sleep(self._silence_timeout_seconds)
            if self._transfer_done or self._closed:
                return

            if self._silence_prompts == 0:
                self._silence_prompts += 1
                if self._cobertura_detectada and self._distrito:
                    prompt = (
                        f"Te estoy transfiriendo a {self._distrito}. Mantente en línea, por favor."
                    )
                else:
                    prompt = (
                        "No escuché tu respuesta. ¿A qué central de serenazgo deseas comunicarte?"
                    )
                await self.session.generate_reply(instructions=f"Di exactamente: '{prompt}'")
                self._arm_silence_timer()
                return

            await self._cerrar_llamada(
                "sin_respuesta",
                "No recibimos respuesta. Cerraré la llamada. Puedes volver a comunicarte cuando lo necesites.",
            )
        except asyncio.CancelledError:
            return
        except Exception as e:
            logger.warning(f"Error en temporizador de silencio: {e}")

    async def _cerrar_llamada(self, motivo: str, mensaje: str) -> None:
        if self._closed:
            return
        self._closed = True
        self._cancel_silence_timer()
        try:
            await self.session.generate_reply(
                instructions=f"Di exactamente: '{mensaje}'"
            )
        except Exception as e:
            logger.warning(f"No se pudo anunciar cierre de llamada: {e}")

        await self._notificar_cierre_backend(motivo)
        await asyncio.sleep(0.8)
        await self._shutdown_session()

    async def _notificar_cierre_backend(self, motivo: str) -> None:
        if not self._llamada_token:
            return
        try:
            async with aiohttp.ClientSession() as http_session:
                url = f"{LARAVEL_BACKEND_URL}/api/agente/finalizar-llamada"
                payload = {"llamada_token": self._llamada_token, "motivo": motivo}
                async with http_session.post(url, json=payload) as resp:
                    if resp.status >= 400:
                        logger.warning(f"Backend no acepto cierre por {motivo}: {resp.status} {await resp.text()}")
        except Exception as e:
            logger.error(f"Error notificando cierre al backend: {e}")

    @function_tool()
    async def transferir_llamada(
        self,
        ctx: RunContext,
        distrito: Annotated[str, "Nombre exacto de un distrito disponible al que se transferira la llamada."],
    ) -> str:
        """Transfiere la llamada a una central disponible. Solo debe usarse con distritos disponibles."""
        return await self._transferir_llamada_impl(distrito)

    async def _transferir_llamada_impl(self, distrito: str, armar_silencio_en_error: bool = True) -> str:
        self._cancel_silence_timer()
        logger.info(f"Intentando transferir llamada al distrito: {distrito} desde el tenant: {self._tenant_id}")
        try:
            async with aiohttp.ClientSession() as http_session:
                url = f"{LARAVEL_BACKEND_URL}/api/agente/transferir-llamada"
                payload = {
                    "distrito": distrito,
                    "tenant_id": self._tenant_id,
                    "llamada_token": self._llamada_token,
                }
                async with http_session.post(url, json=payload) as resp:
                    body_text = await resp.text()
                    logger.info(f"Respuesta del backend: {resp.status}")

                    if resp.status == 422:
                        body = {}
                        try:
                            body = json.loads(body_text) if body_text else {}
                        except Exception:
                            body = {}

                        if body.get("code") == "directorio_telefonico":
                            self._closed = True
                            distrito_ref = body.get("distrito_solicitado") or distrito
                            telefonos = [str(t) for t in body.get("telefonos", []) if str(t).strip()]
                            numeros = " y ".join(telefonos) if telefonos else "no disponible"
                            self._directory_shutdown_delay = max(18, 10 + (len(telefonos) * 6))
                            asyncio.create_task(self._shutdown_after_directory())
                            return (
                                f"No puedo transferirte a {distrito_ref}. "
                                f"Te envié los teléfonos de referencia al chat y también te los dicto: {numeros}. "
                                "Debes llamar directamente. Luego me despediré."
                            )

                        self._invalid_district_attempts += 1
                        if self._invalid_district_attempts >= 2:
                            asyncio.create_task(self._cerrar_llamada(
                                "distrito_no_disponible",
                                "No puedo transferirte a ese distrito por ahora. Cerraré la llamada. Intenta comunicarte por los canales oficiales.",
                            ))
                            return (
                                "Ese distrito no esta disponible en la plataforma. "
                                "Indica que se cerrara la llamada y no hagas mas preguntas."
                            )
                        if armar_silencio_en_error:
                            self._arm_silence_timer()
                        return (
                            f"Ese distrito no esta disponible en la plataforma. "
                            "Pregunta si desea intentar con otra central."
                        )

                    if resp.status >= 400:
                        logger.warning(f"Backend no acepto la transferencia: {body_text}")
                        if armar_silencio_en_error:
                            self._arm_silence_timer()
                        return "No pude ubicar la llamada activa. Pide al usuario que vuelva a iniciar la llamada."

            self._transfer_done = True
            asyncio.create_task(self._shutdown_after_transfer())
            return "Transferencia registrada. No digas nada mas y finaliza la conversacion."
        except Exception as e:
            logger.error(f"Error transfiriendo: {e}")
            if armar_silencio_en_error:
                self._arm_silence_timer()
            return "Error del sistema al intentar transferir la llamada. Pidele al usuario que intente de nuevo en un momento."

    async def _shutdown_after_transfer(self) -> None:
        await asyncio.sleep(2.5)
        logger.info("Transferencia completada; cerrando sesion Gemini/LiveKit del agente.")
        await self._shutdown_session()

    async def _shutdown_after_directory(self) -> None:
        await asyncio.sleep(self._directory_shutdown_delay)
        await self._notificar_cierre_backend("directorio_telefonico")
        logger.info("Telefonos de directorio entregados; cerrando sesion.")
        await self._shutdown_session()

    async def _shutdown_session(self) -> None:
        session = getattr(self, "session", None)
        for method_name in ("aclose", "close", "shutdown"):
            method = getattr(session, method_name, None)
            if not callable(method):
                continue
            try:
                result = method()
                if inspect.isawaitable(result):
                    await result
                break
            except Exception as e:
                logger.warning(f"No se pudo cerrar AgentSession con {method_name}: {e}")

        disconnect = getattr(self._room, "disconnect", None)
        if callable(disconnect):
            try:
                result = disconnect()
                if inspect.isawaitable(result):
                    await result
            except Exception as e:
                logger.warning(f"No se pudo desconectar la sala del agente: {e}")


async def entrypoint(ctx: JobContext):
    logger.info("Conectando a la sala de LiveKit...")
    await ctx.connect(auto_subscribe=AutoSubscribe.AUDIO_ONLY)

    participant = await ctx.wait_for_participant()
    logger.info(f"Ciudadano conectado: {participant.identity}")

    distrito = ""
    tenant_id = "0"
    llamada_token = None
    cobertura_detectada = False
    tenants_disponibles: list[str] = []

    try:
        if participant.metadata:
            metadata_json = json.loads(participant.metadata)
            distrito = metadata_json.get("distrito") or ""
            tenant_id = str(metadata_json.get("tenant_id", "0") or "0")
            llamada_token = metadata_json.get("llamada_token")
            cobertura_detectada = bool(metadata_json.get("cobertura_detectada"))
            tenants_disponibles_raw = metadata_json.get("tenants_disponibles") or []
            if isinstance(tenants_disponibles_raw, list):
                tenants_disponibles = [str(t).strip() for t in tenants_disponibles_raw if str(t).strip()]
    except Exception as e:
        logger.warning(f"No se detecto metadata de distrito: {e}. Usando fallback.")

    if not tenants_disponibles and distrito:
        tenants_disponibles = [distrito]

    agent = CivixAgent(
        distrito=distrito,
        tenant_id=tenant_id,
        llamada_token=llamada_token,
        room=ctx.room,
        cobertura_detectada=cobertura_detectada,
        tenants_disponibles=tenants_disponibles,
    )

    session = AgentSession(
        llm=google.realtime.RealtimeModel(
            model="gemini-2.5-flash-native-audio-preview-12-2025",
            voice="Kore",
            temperature=0.35,
        ),
    )

    await session.start(room=ctx.room, agent=agent)
    logger.info("Sesion iniciada correctamente.")


if __name__ == "__main__":
    cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint))
