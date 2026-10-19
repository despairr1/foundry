import { FoundryConfig } from "../config";
import { EconomicsEngine } from "../engine/economics";
import { logger, withExponentialBackoff } from "../utils/logger";
import { postJson } from "../swarm/providers";

export interface EmbeddingResult {
  vector: number[];
  /** Vector-space identifier stored alongside the vector; only same-model vectors are compared. */
  model: string;
}

const LOCAL_EMBEDDING_MODEL = "local-trigram-hash-128";

export class EmbeddingService {
  private config: FoundryConfig;
  private economics: EconomicsEngine;
  private readonly dimension = 128;
  private remoteDisabled = false;

  constructor(config: FoundryConfig, economics: EconomicsEngine) {
    this.config = config;
    this.economics = economics;
  }

  /** The vector space queries will use; lets retrieval filter stored episodes before embedding. */
  public activeModel(): string {
    return this.useRemote() ? `gemini:${this.config.geminiEmbeddingModel}:${this.dimension}` : LOCAL_EMBEDDING_MODEL;
  }

  public async embedText(text: string): Promise<EmbeddingResult> {
    if (!this.useRemote()) {
      return { vector: this.computeDeterministicVector(text), model: LOCAL_EMBEDDING_MODEL };
    }

    const model = this.config.geminiEmbeddingModel;
    try {
      const values = await withExponentialBackoff(
        "GeminiEmbedding",
        async () => {
          this.economics.enforceBudgetPreFlight("gemini_embedding", model, text, 0);
          const data = await postJson<{ embedding?: { values?: number[] } }>(
            "GeminiEmbedding",
            `${this.config.geminiBaseUrl}/models/${model}:embedContent`,
            { "x-goog-api-key": this.config.geminiApiKey },
            {
              model: `models/${model}`,
              content: { parts: [{ text }] },
              outputDimensionality: this.dimension
            },
            this.config.requestTimeoutMs
          );
          const vector = data.embedding?.values;
          if (!vector || vector.length === 0) {
            throw new Error("Empty embedding returned by Gemini API");
          }
          this.economics.recordUsage("gemini_embedding", model, this.economics.estimateTokens(text), 0);
          return vector;
        },
        { maxAttempts: this.config.requestMaxRetries + 1 }
      );
      return { vector: values, model: this.activeModel() };
    } catch (err) {
      if (err instanceof Error && err.name === "BudgetExceededError") throw err;
      // Switch to local vectors for the rest of the run so stored and queried vectors stay in one space.
      this.remoteDisabled = true;
      logger.warn("Memory", `Gemini embeddings unavailable (${err instanceof Error ? err.message : String(err)}); using local vectors.`);
      return { vector: this.computeDeterministicVector(text), model: LOCAL_EMBEDDING_MODEL };
    }
  }

  private useRemote(): boolean {
    return !this.config.offlineMode && Boolean(this.config.geminiApiKey) && !this.remoteDisabled;
  }

  public computeDeterministicVector(text: string): number[] {
    const vec = new Float32Array(this.dimension);
    const normalized = text.toLowerCase().replace(/\s+/g, " ").trim();

    for (let i = 0; i < normalized.length; i += 1) {
      const c1 = normalized.charCodeAt(i);
      const c2 = i + 1 < normalized.length ? normalized.charCodeAt(i + 1) : 0;
      const c3 = i + 2 < normalized.length ? normalized.charCodeAt(i + 2) : 0;
      const hash = ((c1 * 31 + c2) * 31 + c3) >>> 0;
      const bucket = hash % this.dimension;
      const sign = (hash & 1) === 0 ? 1 : -1;
      vec[bucket] += sign * 1.0;
    }

    let norm = 0;
    for (let i = 0; i < this.dimension; i += 1) {
      norm += vec[i] * vec[i];
    }
    const scale = norm > 0 ? 1 / Math.sqrt(norm) : 1;
    return Array.from(vec, (v) => v * scale);
  }
}
