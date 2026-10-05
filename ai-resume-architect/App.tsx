import React, { useState } from 'react';
import ResumeForm from './components/ResumeForm';
import ResumePreview from './components/ResumePreview';
import UploadResume from './components/UploadResume';
import { ResumeData } from './types';

import { generateProfessionalResume, convertExistingResume } from './services/geminiService';

const App: React.FC = () => {
  const [isGenerating, setIsGenerating] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [generatedResume, setGeneratedResume] = useState<any | null>(null);

  // ✅ FIXED — only ONE view state
  const [view, setView] = useState<'form' | 'upload' | 'preview'>('form');

  /* =========================
     FORM → GENERATE
  ========================= */
  const handleGenerate = async (data: ResumeData) => {
    setIsGenerating(true);
    setError(null);

    try {
      const generated = await generateProfessionalResume(data);

      // Backend may return { resumeData }
      const finalData = generated.resumeData || generated;

      setGeneratedResume(finalData);
      setView('preview');
    } catch (err: any) {
      console.error(err);
      setError(err.message || "Failed to generate resume.");
    } finally {
      setIsGenerating(false);
    }
  };

  /* =========================
     UPLOAD → CONVERT
  ========================= */
  const handleUpload = async (file: File) => {
    setIsGenerating(true);
    setError(null);

    try {
      const converted = await convertExistingResume(file);
      setGeneratedResume(converted);
      setView('preview');
    } catch (err) {
      console.error(err);
      setError("Failed to convert resume.");
    } finally {
      setIsGenerating(false);
    }
  };

  return (
    <div className="min-h-screen bg-slate-50">

      {/* ================= HEADER ================= */}
      <header className="bg-white border-b border-slate-200 sticky top-0 z-10">
        <div className="max-w-7xl mx-auto px-6 py-4 flex items-center justify-between">
          <h1 className="text-xl font-bold text-indigo-600">
            Rahul Standard Resume Builder
          </h1>

          <div className="flex gap-4">
            <button
              onClick={() => setView('form')}
              className={`px-4 py-2 rounded ${
                view === 'form'
                  ? 'bg-indigo-600 text-white'
                  : 'bg-slate-200'
              }`}
            >
              Create Resume
            </button>

            <button
              onClick={() => setView('upload')}
              className={`px-4 py-2 rounded ${
                view === 'upload'
                  ? 'bg-indigo-600 text-white'
                  : 'bg-slate-200'
              }`}
            >
              Upload Resume
            </button>
          </div>
        </div>
      </header>

      {/* ================= MAIN ================= */}
      <main className="max-w-5xl mx-auto px-6 py-10">

        {/* ERROR MESSAGE */}
        {error && (
          <div className="mb-6 p-4 bg-red-100 text-red-700 rounded-lg">
            {error}
          </div>
        )}

        {/* FORM VIEW */}
        {view === 'form' && (
          <ResumeForm
            onSubmit={handleGenerate}
            isLoading={isGenerating}
          />
        )}

        {/* UPLOAD VIEW */}
        {view === 'upload' && (
          <UploadResume
            onUpload={handleUpload}   // ✅ now using correct function
            isLoading={isGenerating}
          />
        )}

        {/* PREVIEW VIEW */}
        {view === 'preview' && generatedResume && (
          <ResumePreview
            data={generatedResume}
            onEdit={() => setView('form')}
          />
        )}
      </main>

      {/* ================= FOOTER ================= */}
      <footer className="text-center py-6 text-sm text-slate-400">
        © 2026 Rahul Standard Resume Builder • Powered by Gemini AI
      </footer>
    </div>
  );
};

export default App;
