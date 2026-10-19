import Database from "better-sqlite3";
import * as fs from "fs";
import * as path from "path";
import { SemanticPatch } from "../ast/semantic_patcher";

export interface EpisodicRecord {
  id?: number;
  language: string;
  moduleName: string;
  failurePattern: string;
  stackTrace: string;
  winningPatches: SemanticPatch[];
  embedding: number[];
  /** Identifies the vector space; vectors from different embedding models are never compared. */
  embeddingModel: string;
  createdAt: string;
}

export interface ScoredMemoryMatch {
  record: EpisodicRecord;
  similarity: number;
}

export class VectorDB {
  private db: Database.Database;

  constructor(sqlitePath: string) {
    fs.mkdirSync(path.dirname(sqlitePath), { recursive: true });
    this.db = new Database(sqlitePath);
    this.initializeSchema();
  }

  private initializeSchema(): void {
    this.db.pragma("journal_mode = WAL");
    this.db.exec(`
      CREATE TABLE IF NOT EXISTS episodic_memory (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        language TEXT NOT NULL,
        module_name TEXT NOT NULL,
        failure_pattern TEXT NOT NULL,
        stack_trace TEXT NOT NULL,
        winning_patches_json TEXT NOT NULL,
        embedding_blob BLOB NOT NULL,
        created_at TEXT NOT NULL
      );
      CREATE INDEX IF NOT EXISTS idx_episodic_language ON episodic_memory(language);
    `);
    const columns = this.db.prepare(`PRAGMA table_info(episodic_memory)`).all() as Array<{ name: string }>;
    if (!columns.some((c) => c.name === "embedding_model")) {
      this.db.exec(`ALTER TABLE episodic_memory ADD COLUMN embedding_model TEXT NOT NULL DEFAULT 'legacy'`);
    }
  }

  public insertEpisode(record: Omit<EpisodicRecord, "id" | "createdAt">): number {
    const floatArray = new Float32Array(record.embedding);
    const buffer = Buffer.from(floatArray.buffer);
    const createdAt = new Date().toISOString();

    const stmt = this.db.prepare(`
      INSERT INTO episodic_memory (
        language,
        module_name,
        failure_pattern,
        stack_trace,
        winning_patches_json,
        embedding_blob,
        embedding_model,
        created_at
      ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    `);

    const info = stmt.run(
      record.language,
      record.moduleName,
      record.failurePattern,
      record.stackTrace,
      JSON.stringify(record.winningPatches),
      buffer,
      record.embeddingModel,
      createdAt
    );

    return Number(info.lastInsertRowid);
  }

  public searchSimilar(
    queryEmbedding: number[],
    embeddingModel: string,
    language?: string,
    topK = 3
  ): ScoredMemoryMatch[] {
    const rows = (
      language
        ? this.db
            .prepare(`SELECT * FROM episodic_memory WHERE embedding_model = ? AND language = ?`)
            .all(embeddingModel, language)
        : this.db.prepare(`SELECT * FROM episodic_memory WHERE embedding_model = ?`).all(embeddingModel)
    ) as Array<Record<string, unknown>>;

    const scored: ScoredMemoryMatch[] = [];

    for (const row of rows) {
      const blob = row.embedding_blob as Buffer;
      // Copy into an aligned buffer: SQLite blobs are not guaranteed to start on a 4-byte boundary.
      const aligned = new Uint8Array(blob.byteLength);
      aligned.set(blob);
      const vector = Array.from(new Float32Array(aligned.buffer, 0, Math.floor(blob.byteLength / Float32Array.BYTES_PER_ELEMENT)));
      const similarity = this.cosineSimilarity(queryEmbedding, vector);

      let winningPatches: SemanticPatch[] = [];
      try {
        winningPatches = JSON.parse(String(row.winning_patches_json)) as SemanticPatch[];
      } catch {
        // A corrupt row should not break retrieval for the rest.
      }

      scored.push({
        similarity,
        record: {
          id: Number(row.id),
          language: String(row.language),
          moduleName: String(row.module_name),
          failurePattern: String(row.failure_pattern),
          stackTrace: String(row.stack_trace),
          winningPatches,
          embedding: vector,
          embeddingModel: String(row.embedding_model),
          createdAt: String(row.created_at)
        }
      });
    }

    scored.sort((a, b) => b.similarity - a.similarity);
    return scored.slice(0, topK);
  }

  public close(): void {
    if (this.db.open) this.db.close();
  }

  private cosineSimilarity(a: number[], b: number[]): number {
    if (a.length !== b.length || a.length === 0) return 0;
    let dot = 0;
    let normA = 0;
    let normB = 0;
    for (let i = 0; i < a.length; i += 1) {
      dot += a[i] * b[i];
      normA += a[i] * a[i];
      normB += b[i] * b[i];
    }
    const denom = Math.sqrt(normA) * Math.sqrt(normB);
    return denom === 0 ? 0 : dot / denom;
  }
}
