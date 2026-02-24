import logging
from openai import OpenAI
from app.config import get_settings

logger = logging.getLogger(__name__)


class OpenRouterService:
    def __init__(self):
        settings = get_settings()
        self.client = OpenAI(
            base_url="https://openrouter.ai/api/v1",
            api_key=settings.openrouter_api_key,
        )
        self.model = settings.openrouter_model

    def summarize_video(self, title: str, description: str) -> str:
        """Gerar resumo de um vídeo usando IA"""
        try:
            prompt = f"""
Gere um resumo conciso do seguinte vídeo do YouTube (3-5 linhas):

Título: {title}

Descrição: {description}

Resumir os principais pontos abordados no vídeo.
"""

            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {
                        "role": "system",
                        "content": "Você é um assistente que resume vídeos do YouTube de forma clara e concisa."
                    },
                    {
                        "role": "user",
                        "content": prompt
                    }
                ],
                max_tokens=500,
                temperature=0.7
            )

            summary = response.choices[0].message.content.strip()
            return summary

        except Exception as e:
            logger.error(f"Erro ao gerar resumo: {e}")
            return "Erro ao gerar resumo."

    def summarize_with_fallback(self, title: str, description: str, video_id: str) -> str:
        """Tentar gerar resumo com fallback para modelos alternativos"""
        models_to_try = [
            self.model,
            "gpt-3.5-turbo",
            "claude-3-haiku"
        ]

        for model in models_to_try:
            try:
                logger.info(f"Tentando modelo: {model}")
                prompt = f"""
Gere um resumo conciso do seguinte vídeo do YouTube (3-5 linhas):

Título: {title}

Descrição: {description}

Resumir os principais pontos abordados no vídeo.
"""

                response = self.client.chat.completions.create(
                    model=model,
                    messages=[
                        {
                            "role": "system",
                            "content": "Você é um assistente que resume vídeos do YouTube de forma clara e concisa."
                        },
                        {
                            "role": "user",
                            "content": prompt
                        }
                    ],
                    max_tokens=500,
                    temperature=0.7
                )

                return response.choices[0].message.content.strip()

            except Exception as e:
                logger.warning(f"Modelo {model} falhou: {e}")
                continue

        return "Erro ao gerar resumo. Nenhum modelo disponível."


openrouter_service = OpenRouterService()
