import asyncio
import os
import json
import logging
import inspect
from typing import Annotated, Optional
from livekit.agents import Agent, AgentSession, AutoSubscribe, JobContext, RunContext, WorkerOptions, cli, function_tool
from livekit.plugins import google
import aiohttp

logger = logging.getLogger("civix-agente")

LARAVEL_BACKEND_URL = os.getenv("LARAVEL_BACKEND_URL", "https://alerta.civix.pe")


class CivixAgent(Agent):
    def __init__(self, distrito: str, tenant_id: str, llamada_token: Optional[str], room):
        self._distrito = distrito
        self._tenant_id = tenant_id
        self._llamada_token = llamada_token
        self._room = room
        self._transfer_done = False

        instructions = (
            f"Eres Civix, la inteligencia artificial de la central de Serenazgo. "
            f"IMPORTANTE: Tu primer mensaje absoluto apenas inicie la conversación DEBE SER EXACTAMENTE ESTE: "
            f"'Hola, te has comunicado a la central de Serenazgo de {distrito}, "
            f"¿desea que lo transfiera a esa central de emergencia para que un operador lo atienda "
            f"o prefiere que le comuniquemos con otro distrito de la provincia de arequipa?'. "
            f"Una vez que digas eso, escucha su respuesta. "
            f"Si acepta {distrito}, usa inmediatamente tu herramienta `transferir_llamada` pasando el distrito '{distrito}'. "
            f"Si pide un distrito diferente, usa `transferir_llamada` con ese nuevo distrito. "
            f"Responde siempre directo al grano y muy corto. "
            f"Después de usar `transferir_llamada`, no sigas conversando ni agregues instrucciones nuevas. "
            f"REGLA CRÍTICA: Habla siempre a un ritmo muy rápido, fluido y dinámico."
        )

        super().__init__(instructions=instructions)

    async def on_enter(self) -> None:
        """Se ejecuta automáticamente cuando el agente se activa."""
        logger.info("Agente activado, enviando saludo inicial...")
        await self.session.generate_reply(
            instructions="Di INMEDIATAMENTE tu mensaje de saludo exacto y hazme la pregunta. No esperes a que el usuario hable primero."
        )

    @function_tool()
    async def transferir_llamada(
        self,
        ctx: RunContext,
        distrito: Annotated[str, "El nombre del distrito al que se va a transferir la llamada, tal cual lo dijo el usuario."],
    ) -> str:
        """Transfiere la llamada a la central de serenazgo. Llama a esta función ÚNICAMENTE cuando el ciudadano ACEPTE ser transferido."""
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
                    logger.info(f"Respuesta del backend: {resp.status}")
                    if resp.status >= 400:
                        body = await resp.text()
                        logger.warning(f"Backend no acepto la transferencia: {body}")
                        return "No pude ubicar la llamada activa. Pide al usuario que vuelva a iniciar la llamada."

                self._transfer_done = True
                asyncio.create_task(self._shutdown_after_transfer())
                return "Transferencia registrada. No digas nada mas y finaliza la conversacion."
        except Exception as e:
            logger.error(f"Error transfiriendo: {e}")
            return "Error del sistema al intentar transferir la llamada. Pídele al usuario que intente de nuevo en un momento."

    async def _shutdown_after_transfer(self) -> None:
        await asyncio.sleep(0.8)
        logger.info("Transferencia completada; cerrando sesion Gemini/LiveKit del agente.")

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

    # Extraer metadata
    metadata_str = participant.metadata
    distrito = "Arequipa"
    tenant_id = "0"
    llamada_token = None

    try:
        if metadata_str:
            metadata_json = json.loads(metadata_str)
            distrito = metadata_json.get("distrito", distrito)
            tenant_id = str(metadata_json.get("tenant_id", "0"))
            llamada_token = metadata_json.get("llamada_token")
    except Exception as e:
        logger.warning(f"No se detectó metadata de distrito: {e}. Usando fallback.")

    # Crear el agente
    agent = CivixAgent(
        distrito=distrito,
        tenant_id=tenant_id,
        llamada_token=llamada_token,
        room=ctx.room,
    )

    # Crear la sesión con el modelo correcto
    session = AgentSession(
        llm=google.realtime.RealtimeModel(
            model="gemini-2.5-flash-native-audio-preview-12-2025",
            voice="Kore",
            temperature=0.4,
        ),
    )

    # Iniciar — on_enter() se ejecuta automáticamente y lanza el saludo
    await session.start(room=ctx.room, agent=agent)
    logger.info("Sesión iniciada correctamente.")


if __name__ == "__main__":
    cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint))
