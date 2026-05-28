import asyncio
import os
import logging
from livekit.agents import AutoSubscribe, JobContext, WorkerOptions, cli
from livekit.agents.multimodal import MultimodalAgent
from livekit.plugins import google

logger = logging.getLogger("mi-agente-serenazgo")

async def entrypoint(ctx: JobContext):
    logger.info("Conectando a la sala de LiveKit...")
    await ctx.connect(auto_subscribe=AutoSubscribe.AUDIO_ONLY)
    logger.info("¡Conectado exitosamente!")

    # Iniciar el modelo Multimodal de Gemini Flash (Ultra rápido y económico)
    model = google.beta.realtime.RealtimeModel(
        model="models/gemini-2.0-flash-exp",
        voice="Aoede", # Opciones: Puck, Charon, Kore, Fenrir, Aoede
        temperature=0.7,
        instructions="Eres Civix, la asistente virtual de la central de emergencias. Escucha al ciudadano, averigua qué emergencia tiene y dile que un operador humano tomará su llamada de inmediato."
    )

    # Esperar a que el usuario entre a la sala
    participant = await ctx.wait_for_participant()

    # Conectar el agente multimodal a la sala
    agent = MultimodalAgent(model=model)
    agent.start(ctx.room, participant)

if __name__ == "__main__":
    cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint))
