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
            # Aquí llamaremos a tu Laravel en el siguiente paso
            async with aiohttp.ClientSession() as session:
                url = f"{LARAVEL_BACKEND_URL}/api/agente/transferir-llamada"
                payload = {"distrito": distrito, "tenant_id": self.tenant_id}
                # Por ahora solo simularemos el éxito hasta que agreguemos la ruta en Laravel
                return f"La llamada ha sido enrutada con éxito a la central de {distrito}. Despídete amablemente y dile al usuario que espere en línea."
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

    # Instrucciones estrictas para el Modelo
    instructions = (
        f"Eres Civix, la inteligencia artificial de la central de Serenazgo. "
        f"El usuario que acaba de conectarse llama desde el distrito de '{distrito}'. "
        f"Al iniciar, tu primer y único mensaje debe ser EXACTAMENTE el siguiente: "
        f"'Hola soy Civix, te has comunicado a la central de Serenazgo de {distrito}, ¿desea que lo transfiera a esa central de emergencia para que un operador lo atienda o prefiere que le comuniquemos con otro distrito de la provincia de Arequipa?'. "
        f"Luego, escucha atentamente. Si acepta o confirma el distrito actual, usa tu herramienta `transferir_llamada` pasando el distrito '{distrito}'. "
        f"Si pide un distrito diferente, pregúntale cuál y luego usa `transferir_llamada` con ese distrito. "
        f"Si el usuario pide algo no relacionado, recuérdale amablemente que eres una central de emergencias. "
        f"Habla siempre en español con voz amable, clara y rápida."
    )

    model = google.beta.realtime.RealtimeModel(
        model="models/gemini-2.5-flash-native-audio-preview-12-2025",
        voice="Puck", 
        temperature=0.6,
        instructions=instructions
    )

    # Crear agente conectando las herramientas (Tools)
    fnc_ctx = CivixHerramientas(tenant_id=tenant_id)
    agent = MultimodalAgent(
        model=model,
        fnc_ctx=fnc_ctx
    )
    agent.start(ctx.room, participant)

    # Empujamos un mensaje interno para FORZAR a que la IA hable primero
    logger.info("Forzando saludo inicial de Civix...")
    agent.chat_ctx.append(
        role="user",
        text="¡Hola! Ya estoy aquí. Salúdame ahora mismo usando tus instrucciones."
    )
    # Iniciar el agente
    agent.start(ctx.room, participant)

    # Pausa de seguridad
    logger.info("Esperando conexión de audio...")
    await asyncio.sleep(2.0)

    # Forzamos a la IA a hablar pasándole la instrucción DIRECTAMENTE al disparador
    logger.info("Forzando saludo inicial de Civix...")
    try:
        await agent.generate_reply(
            instructions="Acabo de conectarme a la llamada. Salúdame INMEDIATAMENTE repitiendo tu saludo exacto para Arequipa."
        )
    except Exception as e:
        logger.error(f"Error forzando saludo: {e}")

if __name__ == "__main__":
    cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint))
