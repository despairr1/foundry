import { VectorDB } from "./db";
import { EmbeddingService } from "./embeddings";
import { SemanticPatch } from "../ast/semantic_patcher";
import { logger } from "../utils/logger";

export class MemoryRetriever {
  private db: VectorDB;
  private embeddings: EmbeddingService;

  constructor(db: VectorDB, embeddings: EmbeddingService) {
    this.db = db;
    this.embeddings = embeddings;
  }

  public async recordWinningEpisode(params: {
    language: string;
    moduleName: string;
    failurePattern: string;
    stackTrace: string;
    winningPatches: SemanticPatch[];
  }): Promise<number> {
    const compositeText = `${params.language} | ${params.moduleName} | ${params.failurePattern} | ${params.stackTrace}`;
    const { vector, model } = await this.embeddings.embedText(compositeText.slice(0, 8000));
    const id = this.db.insertEpisode({
      language: params.language,
      moduleName: params.moduleName,
      failurePattern: params.failurePattern,
      stackTrace: params.stackTrace,
      winningPatches: params.winningPatches,
      embedding: vector,
      embeddingModel: model
    });
    logger.info("Memory", `Indexed winning semantic patch episode #${id} for ${params.moduleName}`);
    return id;
  }

  public async retrieveRelevantFixes(querySpec: string, language: string, topK = 3): Promise<string> {
    const { vector, model } = await this.embeddings.embedText(`${language} | ${querySpec}`.slice(0, 8000));
    const matches = this.db.searchSimilar(vector, model, language, topK);

    const relevant = matches.filter((m) => m.similarity >= 0.15);
    if (relevant.length === 0) {
      return "NO_PRIOR_EPISODIC_MATCHES";
    }

    return relevant
      .map((m, idx) => {
        const patchPreview = m.record.winningPatches
          .map((p) => `${p.action} on ${p.target || "(file)"} in ${p.filePath}`)
          .join(", ");
        return [
          `Episode #${idx + 1} (Similarity: ${m.similarity.toFixed(3)}, Module: ${m.record.moduleName})`,
          `  Failure Pattern: ${m.record.failurePattern.slice(0, 400)}`,
          `  Stack Trace: ${m.record.stackTrace.slice(0, 240)}`,
          `  Proven Fix: ${patchPreview}`
        ].join("\n");
      })
      .join("\n\n");
  }
}
