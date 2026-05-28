import asyncio
import os
import json
import logging
from typing import Annotated
from livekit.agents import Agent, AgentSession, AutoSubscribe, JobContext, RunContext, WorkerOptions, cli, function_tool
from livekit.plugins import google
import aiohttp

logger = logging.getLogger("civix-agente")

LARAVEL_BACKEND_URL = os.getenv("LARAVEL_BACKEND_URL", "https://alerta.civix.pe")


class CivixAgent(Agent):
    def __init__(self, distrito: str, tenant_id: str):
        self._distrito = distrito
        self._tenant_id = tenant_id

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
                payload = {"distrito": distrito, "tenant_id": self._tenant_id}
                async with http_session.post(url, json=payload) as resp:
                    logger.info(f"Respuesta del backend: {resp.status}")
                return (
                    f"La llamada ha sido enrutada con éxito a la central de {distrito}. "
                    f"Despídete amablemente, indícale al usuario que espere en la línea "
                    f"y finaliza la conversación."
                )
        except Exception as e:
            logger.error(f"Error transfiriendo: {e}")
            return "Error del sistema al intentar transferir la llamada. Pídele al usuario que intente de nuevo en un momento."


async def entrypoint(ctx: JobContext):
    logger.info("Conectando a la sala de LiveKit...")
    await ctx.connect(auto_subscribe=AutoSubscribe.AUDIO_ONLY)

    participant = await ctx.wait_for_participant()
    logger.info(f"Ciudadano conectado: {participant.identity}")

    # Extraer metadata
    metadata_str = participant.metadata
    distrito = "Arequipa"
    tenant_id = "0"

    try:
        if metadata_str:
            metadata_json = json.loads(metadata_str)
            distrito = metadata_json.get("distrito", distrito)
            tenant_id = str(metadata_json.get("tenant_id", "0"))
    except Exception as e:
        logger.warning(f"No se detectó metadata de distrito: {e}. Usando fallback.")

    # Crear el agente
    agent = CivixAgent(distrito=distrito, tenant_id=tenant_id)

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
