/**
 * @license
 * SPDX-License-Identifier: Apache-2.0
 */

import { GoogleGenAI, Type } from "@google/genai";
import { ResumeData } from "../ai-resume-architect_good/frontend_final/src/types";

// Initialize Gemini API
const ai = new GoogleGenAI({ 
  apiKey: process.env.GEMINI_API_KEY || "" 
});

const RESUME_PARSER_PROMPT = `
You are an expert resume parser.

Extract and return the following information from the provided resume:
- name
- summary
- skills
- tools
- experience (company, role, duration, points)
- projects (title, description)
- education
- certifications

Return ONLY JSON.
Do NOT generate fake content.
Use only provided data.
`;

export const geminiService = {
  /**
   * Parse resume text or file using Gemini
   */
  parseResume: async (content: string | { data: string, mimeType: string }): Promise<ResumeData> => {
    try {
      const promptPart = { text: RESUME_PARSER_PROMPT };
      const contentPart = typeof content === 'string' 
        ? { text: `Resume text to parse:\n\n${content}` }
        : { inlineData: content };

      const response = await ai.models.generateContent({
        model: "gemini-3-flash-preview",
        contents: { parts: [promptPart, contentPart] },
        config: {
          responseMimeType: "application/json",
          responseSchema: {
            type: Type.OBJECT,
            properties: {
              name: { type: Type.STRING },
              summary: { type: Type.STRING },
              skills: { 
                type: Type.ARRAY,
                items: { type: Type.STRING }
              },
              tools: { 
                type: Type.ARRAY,
                items: { type: Type.STRING }
              },
              experience: {
                type: Type.ARRAY,
                items: {
                  type: Type.OBJECT,
                  properties: {
                    company: { type: Type.STRING },
                    role: { type: Type.STRING },
                    duration: { type: Type.STRING },
                    points: { 
                      type: Type.ARRAY,
                      items: { type: Type.STRING }
                    }
                  },
                  required: ["company", "role", "duration", "points"]
                }
              },
              education: { 
                type: Type.ARRAY,
                items: { type: Type.STRING }
              },
              certifications: { 
                type: Type.ARRAY,
                items: { type: Type.STRING }
              },
              projects: {
                type: Type.ARRAY,
                items: {
                  type: Type.OBJECT,
                  properties: {
                    title: { type: Type.STRING },
                    description: { type: Type.STRING }
                  },
                  required: ["title", "description"]
                }
              }
            },
            required: ["summary", "skills", "experience", "education"]
          }
        }
      });

      const result = JSON.parse(response.text || "{}");
      
      // Ensure all fields are present to match ResumeData interface
      return {
        name: result.name || "",
        summary: result.summary || "",
        skills: result.skills || [],
        tools: result.tools || [],
        experience: result.experience || [],
        education: result.education || [],
        certifications: result.certifications || [],
        projects: result.projects || []
      };
    } catch (error) {
      console.error("Error parsing resume with Gemini:", error);
      throw new Error("Failed to parse resume content. Please ensure the file is readable.");
    }
  }
};
