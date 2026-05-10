from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "sqlite+aiosqlite:///./searchengine.db"
    crawl_concurrency: int = 20
    request_timeout: int = 30
    max_depth: int = 3
    max_pages_per_domain: int = 1000
    user_agent: str = "AISearchBot/1.0 (Research Indexing Bot)"
    ai_keywords: list[str] = [
        "artificial intelligence", "machine learning", "deep learning", "neural network",
        "large language model", "llm", "transformer", "gpt", "claude", "gemini",
        "fine-tuning", "rlhf", "benchmark", "dataset", "pytorch", "tensorflow",
        "jax", "cuda", "gpu", "training", "inference", "model", "alignment",
        "safety", "agent", "rag", "embedding", "vector", "diffusion", "mlp",
        "cnn", "rnn", "lstm", "generative ai", "genai", "prompt engineering",
        "quantization", "distillation", "multimodal", "attention", "backpropagation",
        "gradient descent", "reinforcement learning", "supervised learning",
        "unsupervised learning", "self-supervised", "few-shot", "zero-shot",
        "chain-of-thought", "evaluation", "hallucination", "token", "tokenizer",
        "paper", "arxiv", "research", "ablation", "sota", "state of the art"
    ]
    seed_urls: list[str] = []
    
    class Config:
        env_file = ".env"


settings = Settings()
