import logging
from openai import OpenAI
from app.config import get_settings

logger = logging.getLogger(__name__)


class OpenRouterService:
    def __init__(self, model: str = None):
        settings = get_settings()
        self.client = OpenAI(
            base_url="https://openrouter.ai/api/v1",
            api_key=settings.openrouter_api_key,
        )
        self.model = (model or settings.openrouter_model).strip()

    def summarize_video(self, title: str, description: str) -> str:
        """Gerar resumo de um vídeo usando IA"""
        try:
            return self._create_completion(
                model=self.model,
                system_prompt=(
                    "Você é um assistente que resume vídeos do YouTube "
                    "de forma clara e concisa."
                ),
                user_prompt=f"""
Gere um resumo conciso do seguinte vídeo do YouTube (3-5 linhas):

Título: {title}

Descrição: {description}

Resumir os principais pontos abordados no vídeo.
""",
                max_tokens=500,
            )

        except Exception as e:
            logger.error(f"Erro ao gerar resumo: {e}")
            return "Erro ao gerar resumo."

    def summarize_transcript(self, title: str, transcript: str) -> str:
        """Gera resumo a partir da transcrição do vídeo."""
        try:
            trimmed_transcript = (transcript or "").strip()[:18000]
            if not trimmed_transcript:
                return "Erro ao gerar resumo."

            return self._create_completion(
                model=self.model,
                system_prompt=(
                    "Você é um assistente que resume vídeos do YouTube com base "
                    "na transcrição, priorizando o conteúdo efetivamente falado."
                ),
                user_prompt=f"""
Gere um resumo claro do vídeo abaixo com base na transcrição.

Título: {title}

Transcrição:
{trimmed_transcript}

Instruções:
- Resuma apenas o conteúdo que aparece na transcrição
- Destaque ideias, argumentos e conclusões principais
- Use português brasileiro
- Seja objetivo, mas informativo
""",
                max_tokens=900,
            )
        except Exception as e:
            logger.error(f"Erro ao gerar resumo por transcript: {e}")
            return "Erro ao gerar resumo."

    def summarize_with_fallback(self, title: str, description: str, video_id: str) -> str:
        """Tentar gerar resumo com fallback para modelos alternativos"""
        models_to_try = []
        for model in [self.model, "gpt-3.5-turbo", "claude-3-haiku"]:
            cleaned = (model or "").strip()
            if cleaned and cleaned not in models_to_try:
                models_to_try.append(cleaned)

        for model in models_to_try:
            try:
                logger.info(f"Tentando modelo: {model}")
                return self._create_completion(
                    model=model,
                    system_prompt=(
                        "Você é um assistente que resume vídeos do YouTube "
                        "de forma clara e concisa."
                    ),
                    user_prompt=f"""
Gere um resumo conciso do seguinte vídeo do YouTube (3-5 linhas):

Título: {title}

Descrição: {description}

Resumir os principais pontos abordados no vídeo.
""",
                    max_tokens=500,
                )

            except Exception as e:
                logger.warning(f"Modelo {model} falhou: {e}")
                continue

        return "Erro ao gerar resumo. Nenhum modelo disponível."

    def summarize_transcript_with_fallback(
        self,
        title: str,
        transcript: str,
        description: str,
        video_id: str,
    ) -> tuple[str, str]:
        models_to_try = []
        for model in [self.model, "gpt-3.5-turbo", "claude-3-haiku"]:
            cleaned = (model or "").strip()
            if cleaned and cleaned not in models_to_try:
                models_to_try.append(cleaned)

        transcript_text = (transcript or "").strip()
        if transcript_text:
            for model in models_to_try:
                try:
                    logger.info("Tentando resumo por transcript com modelo: %s", model)
                    summary = self._create_completion(
                        model=model,
                        system_prompt=(
                            "Você é um assistente que resume vídeos do YouTube com base "
                            "na transcrição, priorizando o conteúdo efetivamente falado."
                        ),
                        user_prompt=f"""
Gere um resumo claro do vídeo abaixo com base na transcrição.

Título: {title}

Transcrição:
{transcript_text[:18000]}

Instruções:
- Resuma apenas o conteúdo que aparece na transcrição
- Destaque ideias, argumentos e conclusões principais
- Use português brasileiro
- Seja objetivo, mas informativo
""",
                        max_tokens=900,
                    )
                    return summary, model
                except Exception as exc:
                    logger.warning("Resumo por transcript falhou no modelo %s: %s", model, exc)

        for model in models_to_try:
            try:
                logger.info("Tentando resumo por metadados com modelo: %s", model)
                summary = self._create_completion(
                    model=model,
                    system_prompt=(
                        "Você é um assistente que resume vídeos do YouTube "
                        "de forma clara e concisa."
                    ),
                    user_prompt=f"""
Gere um resumo conciso do seguinte vídeo do YouTube (3-5 linhas):

Título: {title}

Descrição: {description}

Resumir os principais pontos abordados no vídeo.
""",
                    max_tokens=500,
                )
                return summary, model
            except Exception as exc:
                logger.warning("Resumo por metadados falhou no modelo %s: %s", model, exc)

        return "Erro ao gerar resumo. Nenhum modelo disponível.", self.model

    def _create_completion(
        self,
        model: str,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int,
    ) -> str:
        response = self.client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            max_tokens=max_tokens,
            temperature=0.7,
        )
        return response.choices[0].message.content.strip()


openrouter_service = OpenRouterService()
