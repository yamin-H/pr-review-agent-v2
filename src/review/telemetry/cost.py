"""Token cost estimation and model pricing tables."""

from typing import NamedTuple


class ModelPricing(NamedTuple):
    """Pricing rates per 1,000,000 tokens in USD."""

    input_cost_per_million: float
    output_cost_per_million: float


# Official pricing schedules per 1M tokens (USD)
MODEL_PRICING: dict[str, ModelPricing] = {
    # Groq OpenAI Open Source models
    "openai/gpt-oss-120b": ModelPricing(input_cost_per_million=0.15, output_cost_per_million=0.60),
    "openai/gpt-oss-20b": ModelPricing(input_cost_per_million=0.075, output_cost_per_million=0.30),
    # Meta Llama models
    "llama-3.3-70b-versatile": ModelPricing(
        input_cost_per_million=0.59, output_cost_per_million=0.79
    ),
    "llama-3.1-70b-versatile": ModelPricing(
        input_cost_per_million=0.59, output_cost_per_million=0.79
    ),
    "llama-3.1-8b-instant": ModelPricing(
        input_cost_per_million=0.05, output_cost_per_million=0.08
    ),
    # Mistral models
    "mixtral-8x7b-32768": ModelPricing(
        input_cost_per_million=0.24, output_cost_per_million=0.24
    ),
}

# Conservative default rates for unlisted models ($0.50 input / $1.00 output per 1M tokens)
DEFAULT_PRICING = ModelPricing(input_cost_per_million=0.50, output_cost_per_million=1.00)


def calculate_cost_usd(
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
) -> float:
    """Calculate the precise dollar cost of an inference completion.

    Uses official token pricing schedules normalized per million tokens.
    """
    pricing = MODEL_PRICING.get(model, DEFAULT_PRICING)
    input_cost = (prompt_tokens / 1_000_000.0) * pricing.input_cost_per_million
    output_cost = (completion_tokens / 1_000_000.0) * pricing.output_cost_per_million
    return round(input_cost + output_cost, 6)
