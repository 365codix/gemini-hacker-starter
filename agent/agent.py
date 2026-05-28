import asyncio
import os
import json
import logging
from livekit.agents import AutoSubscribe, JobContext, WorkerOptions, cli, llm
from livekit.agents.multimodal import MultimodalAgent
from livekit.plugins import google
import aiohttp

logger = logging.getLogger("civix-agente")

# Dominio del backend (Puedes configurarlo en tu EasyPanel como variable de entorno)
LARAVEL_BACKEND_URL = os.getenv("LARAVEL_BACKEND_URL", "https://alerta.civix.pe")

class CivixHerramientas(llm.FunctionContext):
    def __init__(self, tenant_id: str):
        super().__init__()
        self.tenant_id = tenant_id

    @llm.ai_callable(description="Transfiere la llamada a la central de serenazgo. Llama a esta función ÚNICAMENTE cuando el ciudadano ACEPTE ser transferido.")
    async def transferir_llamada(
        self,
        distrito: str = llm.TypeInfo(description="El nombre del distrito al que se va a transferir la llamada, tal cual lo dijo el usuario."),
    ):
        logger.info(f"Intentando transferir llamada al distrito: {distrito} desde el tenant: {self.tenant_id}")
        try:
            async with aiohttp.ClientSession() as session:
                url = f"{LARAVEL_BACKEND_URL}/api/agente/transferir-llamada"
                payload = {"distrito": distrito, "tenant_id": self.tenant_id}
                return f"La llamada ha sido enrutada con éxito a la central de {distrito}. Despídete amablemente, indícale al usuario que espere en la línea y finaliza la conversación."
        except Exception as e:
            logger.error(f"Error transfiriendo: {e}")
            return "Error del sistema al intentar transferir la llamada. Pídele al usuario que intente de nuevo en un momento."

async def entrypoint(ctx: JobContext):
    logger.info("Conectando a la sala de LiveKit...")
    await ctx.connect(auto_subscribe=AutoSubscribe.AUDIO_ONLY)

    # Esperamos a que entre el ciudadano
    participant = await ctx.wait_for_participant()
    logger.info(f"Ciudadano conectado: {participant.identity}")

    # Extraer el distrito de la metadata que enviará Laravel
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

    # Instrucciones estrictas (Enfoque Zero-Tokens: el navegador ya hizo el saludo largo)
    instructions = (
        f"Eres Civix, la inteligencia artificial de la central de Serenazgo. "
        f"Al usuario que acaba de conectarse ya se le dio la bienvenida automáticamente y "
        f"la máquina le acaba de preguntar: '¿Desea que lo transfiera a la central de {distrito} o prefiere otro distrito?'. "
        f"Tu tarea principal es escuchar SU RESPUESTA a esa pregunta que ya escuchó. "
        f"Si acepta o dice que sí quiere {distrito}, usa inmediatamente tu herramienta `transferir_llamada` pasando el distrito '{distrito}'. "
        f"Si pide un distrito diferente, usa `transferir_llamada` con ese nuevo distrito. "
        f"Responde siempre directo al grano y muy corto. NUNCA te vuelvas a presentar. "
        f"IMPORTANTE: Habla siempre a un ritmo muy rápido, fluido y dinámico. Evita pausas largas y acelera tu forma de hablar."
    )

    model = google.beta.realtime.RealtimeModel(
        model="models/gemini-2.5-flash-native-audio-preview-12-2025",
        voice="Aoede", # <-- Voz FEMENINA, energética y rápida
        temperature=0.6, # <-- Temperatura baja para respuestas directas
        instructions=instructions
    )

    # Crear agente conectando las herramientas (Tools)
    fnc_ctx = CivixHerramientas(tenant_id=tenant_id)
    agent = MultimodalAgent(
        model=model,
        fnc_ctx=fnc_ctx
    )
    
    # Iniciamos el agente y lo dejamos esperando tranquilamente la respuesta del usuario
    logger.info("Iniciando agente. Esperando respuesta del ciudadano al TTS del navegador...")
    agent.start(ctx.room, participant)

if __name__ == "__main__":
    cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint))
