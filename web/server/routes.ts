import type { Express, Request, Response } from "express";
import { createServer } from "node:http";
import type { Server } from "node:http";
import { storage } from "./storage";
import { chatRequestSchema } from "@shared/schema";
import { getPersona, PERSONA_LIST } from "./personas";
import OpenAI from "openai";

// LLM_API_KEY is the canonical name. OPENAI_API_KEY is honored as fallback.
// LLM_BASE_URL defaults to Gemini's free tier (1500 req/day on Flash, no card).
// To use OpenAI proper:  LLM_BASE_URL=https://api.openai.com/v1/  + an OpenAI key.
// To use OpenRouter:     LLM_BASE_URL=https://openrouter.ai/api/v1/  + an OR key.
const llmApiKey = process.env.LLM_API_KEY ?? process.env.OPENAI_API_KEY;
if (!llmApiKey) {
  console.warn(
    "[meat-suit-web] LLM_API_KEY is unset. /api/chat will return model_unavailable until it's set. Get a free Gemini key at https://aistudio.google.com/apikey"
  );
}
const llmBaseURL =
  process.env.LLM_BASE_URL ??
  "https://generativelanguage.googleapis.com/v1beta/openai/";
const llmModel = process.env.LLM_MODEL ?? "gemini-2.0-flash";

const openai = new OpenAI({
  apiKey: llmApiKey ?? "missing",
  baseURL: llmBaseURL,
});

export async function registerRoutes(
  httpServer: Server,
  app: Express
): Promise<Server> {
  // List personas (display info only, never the system prompts).
  app.get("/api/personas", (_req: Request, res: Response) => {
    res.json(
      PERSONA_LIST.map((p) => ({
        key: p.key,
        display: p.display,
        blurb: p.blurb,
        sample: p.sample,
      }))
    );
  });

  // Get history for a session.
  app.get("/api/sessions/:id/messages", async (req: Request, res: Response) => {
    const id = String(req.params.id);
    const messages = await storage.getMessages(id);
    res.json(messages);
  });

  // Reset a session (clear its history).
  app.delete("/api/sessions/:id", async (req: Request, res: Response) => {
    const id = String(req.params.id);
    await storage.deleteSession(id);
    res.json({ ok: true });
  });

  // Chat: persist user message, call OpenAI, persist assistant reply, return reply.
  app.post("/api/chat", async (req: Request, res: Response) => {
    const parsed = chatRequestSchema.safeParse(req.body);
    if (!parsed.success) {
      return res.status(400).json({ error: "invalid_request", details: parsed.error.flatten() });
    }
    const { sessionId, persona: personaKey, message } = parsed.data;

    const persona = getPersona(personaKey);
    const now = Date.now();

    await storage.ensureSession({
      id: sessionId,
      persona: personaKey,
      createdAt: now,
    });
    await storage.addMessage({
      sessionId,
      role: "user",
      content: message,
      createdAt: now,
    });

    const history = await storage.getMessages(sessionId);
    // Last ~20 turns is plenty. Persona prompt becomes the system message;
    // the recent conversation goes in as a single user-role block so the
    // existing Speaker: prefix scheme still works inside it.
    const turns = history.slice(-20);
    const conversationBlock = turns
      .map((m) => {
        const speaker = m.role === "user" ? "User" : persona.display;
        return `${speaker}: ${m.content}`;
      })
      .join("\n\n");

    let reply = "";
    try {
      // Chat Completions works against any OpenAI-compatible endpoint
      // (OpenAI, Gemini's compat shim, Groq, OpenRouter, vLLM, Ollama).
      // Responses API was OpenAI-only and Gemini's compat layer doesn't expose it.
      const response = await openai.chat.completions.create({
        model: llmModel,
        messages: [
          { role: "system", content: persona.systemPrompt },
          {
            role: "user",
            content: `Conversation so far:\n\n${conversationBlock}\n\nReply as ${persona.display}.`,
          },
        ],
      });
      reply = response.choices[0]?.message?.content?.trim() ?? "";
    } catch (err) {
      console.error("llm_error", err);
      return res.status(502).json({ error: "model_unavailable" });
    }

    if (!reply) {
      reply = "...";
    }

    const assistantMsg = await storage.addMessage({
      sessionId,
      role: "assistant",
      content: reply,
      createdAt: Date.now(),
    });

    res.json({ message: assistantMsg });
  });

  return httpServer;
}
