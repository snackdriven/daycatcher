import { chatSessions, messages } from "@shared/schema";
import type {
  ChatSession,
  Message,
  InsertSession,
  InsertMessage,
} from "@shared/schema";
import { drizzle } from "drizzle-orm/better-sqlite3";
import Database from "better-sqlite3";
import { eq, asc } from "drizzle-orm";

const sqlite = new Database("data.db");
sqlite.pragma("journal_mode = WAL");

// Bootstrap tables for fresh databases (drizzle-kit push isn't run at deploy).
sqlite.exec(`
  CREATE TABLE IF NOT EXISTS chat_sessions (
    id TEXT PRIMARY KEY,
    persona TEXT NOT NULL,
    created_at INTEGER NOT NULL
  );
  CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at INTEGER NOT NULL
  );
  CREATE INDEX IF NOT EXISTS idx_messages_session
    ON messages(session_id, created_at);
`);

export const db = drizzle(sqlite);

export interface IStorage {
  ensureSession(input: InsertSession): Promise<ChatSession>;
  getSession(id: string): Promise<ChatSession | undefined>;
  getMessages(sessionId: string): Promise<Message[]>;
  addMessage(input: InsertMessage): Promise<Message>;
  deleteSession(id: string): Promise<void>;
}

export class DatabaseStorage implements IStorage {
  async ensureSession(input: InsertSession): Promise<ChatSession> {
    const existing = db
      .select()
      .from(chatSessions)
      .where(eq(chatSessions.id, input.id))
      .get();
    if (existing) return existing;
    return db.insert(chatSessions).values(input).returning().get();
  }

  async getSession(id: string): Promise<ChatSession | undefined> {
    return db.select().from(chatSessions).where(eq(chatSessions.id, id)).get();
  }

  async getMessages(sessionId: string): Promise<Message[]> {
    return db
      .select()
      .from(messages)
      .where(eq(messages.sessionId, sessionId))
      .orderBy(asc(messages.createdAt))
      .all();
  }

  async addMessage(input: InsertMessage): Promise<Message> {
    return db.insert(messages).values(input).returning().get();
  }

  async deleteSession(id: string): Promise<void> {
    db.delete(messages).where(eq(messages.sessionId, id)).run();
    db.delete(chatSessions).where(eq(chatSessions.id, id)).run();
  }
}

export const storage = new DatabaseStorage();
