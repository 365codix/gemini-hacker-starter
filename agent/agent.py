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

        distritos = self._format_districts()
        destino_detectado = self._distrito if self._cobertura_detectada and self._distrito else ""
        if destino_detectado:
            first_prompt = (
                f"Hola, te has comunicado a la central de Serenazgo de {destino_detectado}. "
                f"¿Deseas que te transfiera a esta central o prefieres otro distrito disponible: {distritos}?"
            )
            routing_rule = (
                f"Si acepta esta central, llama `transferir_llamada` con '{destino_detectado}'. "
                f"Si pide otro distrito, solo transfiere si coincide exactamente con uno de estos: {distritos}."
            )
        else:
            first_prompt = (
                f"Hola, no pude detectar una central con cobertura para tu ubicación. "
                f"Puedo comunicarte con estos distritos disponibles: {distritos}. ¿A cuál deseas que te transfiera?"
            )
            routing_rule = f"Solo transfiere si el ciudadano elige exactamente uno de estos distritos disponibles: {distritos}."

        instructions = (
            "Eres Civix, la inteligencia artificial de la central de Serenazgo. "
            f"Tu primer mensaje absoluto debe ser: '{first_prompt}'. "
            f"{routing_rule} "
            "Si el ciudadano pide un distrito que no está disponible, NO transfieras: dile brevemente que ese distrito aún no está en la plataforma "
            f"y ofrece solo estas opciones: {distritos}. "
            "Si el ciudadano no responde a la pregunta de transferencia, se le repreguntará una sola vez y luego el sistema cerrará la llamada. "
            "Responde siempre directo al grano, muy corto y con ritmo rápido. "
            "Después de usar `transferir_llamada`, no sigas conversando ni agregues instrucciones nuevas."
        )

        super().__init__(instructions=instructions)

    async def on_enter(self) -> None:
        logger.info("Agente activado, enviando saludo inicial...")
        await self.session.generate_reply(
            instructions="Di INMEDIATAMENTE tu primer mensaje exacto y haz la pregunta de transferencia."
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
            await asyncio.sleep(7)
            if self._transfer_done or self._closed:
                return

            if self._silence_prompts == 0:
                self._silence_prompts += 1
                if self._cobertura_detectada and self._distrito:
                    prompt = (
                        f"No escuché tu respuesta. ¿Te transfiero a {self._distrito} "
                        f"o a otro distrito disponible: {self._format_districts()}?"
                    )
                else:
                    prompt = (
                        f"No escuché tu respuesta. Indica uno de estos distritos disponibles: "
                        f"{self._format_districts()}."
                    )
                await self.session.generate_reply(instructions=f"Di exactamente: '{prompt}'")
                self._arm_silence_timer()
                return

            await self._cerrar_por_silencio()
        except asyncio.CancelledError:
            return
        except Exception as e:
            logger.warning(f"Error en temporizador de silencio: {e}")

    async def _cerrar_por_silencio(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._cancel_silence_timer()
        try:
            await self.session.generate_reply(
                instructions="Di exactamente: 'No recibimos respuesta. Cerraré la llamada. Puedes volver a comunicarte cuando lo necesites.'"
            )
        except Exception as e:
            logger.warning(f"No se pudo anunciar cierre por silencio: {e}")

        await self._notificar_cierre_backend("sin_respuesta")
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
                        self._invalid_district_attempts += 1
                        if self._invalid_district_attempts >= 2:
                            asyncio.create_task(self._cerrar_por_silencio())
                            return (
                                "Ese distrito no esta disponible en la plataforma. "
                                "Indica que se cerrara la llamada y no hagas mas preguntas."
                            )
                        self._arm_silence_timer()
                        return (
                            f"Ese distrito no esta disponible en la plataforma. "
                            f"Ofrece solo estas opciones: {self._format_districts()}."
                        )

                    if resp.status >= 400:
                        logger.warning(f"Backend no acepto la transferencia: {body_text}")
                        self._arm_silence_timer()
                        return "No pude ubicar la llamada activa. Pide al usuario que vuelva a iniciar la llamada."

            self._transfer_done = True
            asyncio.create_task(self._shutdown_after_transfer())
            return "Transferencia registrada. No digas nada mas y finaliza la conversacion."
        except Exception as e:
            logger.error(f"Error transfiriendo: {e}")
            self._arm_silence_timer()
            return "Error del sistema al intentar transferir la llamada. Pidele al usuario que intente de nuevo en un momento."

    async def _shutdown_after_transfer(self) -> None:
        await asyncio.sleep(0.8)
        logger.info("Transferencia completada; cerrando sesion Gemini/LiveKit del agente.")
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
