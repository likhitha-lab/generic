import React, { useState } from "react";
import { convertExistingResume } from "../services/geminiService";

interface Props {
  onConverted: (data: any) => void;
}

const UploadResume: React.FC<Props> = ({ onConverted }) => {
  const [file, setFile] = useState<File | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleUpload = async () => {
    if (!file) {
      setError("Please select a file first.");
      return;
    }

    setLoading(true);
    setError(null);

    try {
      const result = await convertExistingResume(file);

      // Backend may return { resumeData: {...} } OR direct object
      const finalData = result.resumeData || result;

      onConverted(finalData);
    } catch (err: any) {
      console.error(err);
      setError("Conversion failed. Please try again.");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="bg-white p-8 rounded-2xl shadow border border-slate-200 max-w-xl mx-auto space-y-6">

      <div>
        <h2 className="text-2xl font-bold text-slate-800">
          Upload Existing Resume
        </h2>
        <p className="text-slate-500 text-sm mt-1">
          Upload your PDF or DOCX resume and convert it into Rahul Standard Format.
        </p>
      </div>

      {/* FILE INPUT */}
      <div className="space-y-2">
        <input
          type="file"
          accept=".pdf,.docx"
          onChange={(e) => setFile(e.target.files?.[0] || null)}
          className="block w-full text-sm text-slate-600
                     file:mr-4 file:py-2 file:px-4
                     file:rounded-lg file:border-0
                     file:text-sm file:font-semibold
                     file:bg-indigo-50 file:text-indigo-600
                     hover:file:bg-indigo-100"
        />

        {file && (
          <p className="text-sm text-green-600">
            Selected: {file.name}
          </p>
        )}
      </div>

      {/* ERROR MESSAGE */}
      {error && (
        <div className="p-3 bg-red-100 text-red-700 rounded-lg text-sm">
          {error}
        </div>
      )}

      {/* BUTTON */}
      <button
        onClick={handleUpload}
        disabled={loading}
        className="w-full px-4 py-3 bg-indigo-600 text-white rounded-xl font-semibold 
                   hover:bg-indigo-700 transition disabled:opacity-50 disabled:cursor-not-allowed"
      >
        {loading ? "Processing Resume..." : "Convert to Rahul Format"}
      </button>
    </div>
  );
};

export default UploadResume;
