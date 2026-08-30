"""Amazon Bedrock adapters."""

from app.adapters.bedrock.embedding import BedrockEmbeddingProvider
from app.adapters.bedrock.llm import BedrockLLMProvider

__all__ = ["BedrockEmbeddingProvider", "BedrockLLMProvider"]
