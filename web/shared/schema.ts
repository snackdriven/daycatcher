import { sqliteTable, text, integer } from "drizzle-orm/sqlite-core";
import { createInsertSchema } from "drizzle-zod";
import { z } from "zod";

// Chat sessions — one per persona per browser session.
// Sessions are anonymous, identified by a client-generated token kept in React state.
export const chatSessions = sqliteTable("chat_sessions", {
  id: text("id").primaryKey(), // uuid generated client-side
  persona: text("persona").notNull(), // smug_construct | feral_bff | stoic_mentor | quiet_guardian
  createdAt: integer("created_at").notNull(),
});

export const messages = sqliteTable("messages", {
  id: integer("id").primaryKey({ autoIncrement: true }),
  sessionId: text("session_id").notNull(),
  role: text("role").notNull(), // 'user' | 'assistant'
  content: text("content").notNull(),
  createdAt: integer("created_at").notNull(),
});

export const insertSessionSchema = createInsertSchema(chatSessions);
export const insertMessageSchema = createInsertSchema(messages).omit({ id: true });

export type ChatSession = typeof chatSessions.$inferSelect;
export type Message = typeof messages.$inferSelect;
export type InsertSession = z.infer<typeof insertSessionSchema>;
export type InsertMessage = z.infer<typeof insertMessageSchema>;

export const PERSONA_KEYS = [
  "smug_construct",
  "feral_bff",
  "stoic_mentor",
  "quiet_guardian",
] as const;

export type PersonaKey = (typeof PERSONA_KEYS)[number];

export const chatRequestSchema = z.object({
  sessionId: z.string().min(1),
  persona: z.enum(PERSONA_KEYS),
  message: z.string().min(1).max(4000),
});

export type ChatRequest = z.infer<typeof chatRequestSchema>;
