import type { Express, Request, Response } from "express";
import { createServer } from "node:http";
import type { Server } from "node:http";
import { storage } from "./storage";
import { chatRequestSchema } from "@shared/schema";
import { getPersona, PERSONA_LIST } from "./personas";
import OpenAI from "openai";

const openai = new OpenAI();

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
    // Build OpenAI Responses API input. Last ~20 turns is plenty.
    const turns = history.slice(-20);
    const conversationBlock = turns
      .map((m) => {
        const speaker = m.role === "user" ? "User" : persona.display;
        return `${speaker}: ${m.content}`;
      })
      .join("\n\n");

    const input = `${persona.systemPrompt}\n\n---\nConversation so far:\n\n${conversationBlock}\n\n${persona.display}:`;

    let reply = "";
    try {
      const response = await openai.responses.create({
        model: process.env.OPENAI_MODEL ?? "gpt-4o-mini",
        input,
      });
      // Extract text. The Responses API exposes output_text in the SDK.
      // Fall back to digging through the structured output if needed.
      reply = (response as any).output_text?.trim?.() ?? "";
      if (!reply && Array.isArray((response as any).output)) {
        for (const item of (response as any).output) {
          if (item.type === "message" && Array.isArray(item.content)) {
            for (const part of item.content) {
              if (part.type === "output_text" && typeof part.text === "string") {
                reply += part.text;
              }
            }
          }
        }
        reply = reply.trim();
      }
    } catch (err) {
      console.error("openai_error", err);
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
