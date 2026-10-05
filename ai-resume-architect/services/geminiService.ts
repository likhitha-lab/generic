const BASE_URL = "https://resume-backend-1069915675190.us-central1.run.app/"; 
// ⚠️ Replace with your real Cloud Run URL

export async function generateProfessionalResume(data: any) {
  const res = await fetch(`${BASE_URL}/generate-resume`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(data),
  });

  if (!res.ok) {
    throw new Error("Failed to generate resume");
  }

  return res.json();
}


export async function convertExistingResume(file: File) {
  const formData = new FormData();
  formData.append("file", file);

  const res = await fetch(`${BASE_URL}/convert-resume`, {
    method: "POST",
    body: formData,
  });

  if (!res.ok) {
    throw new Error("Failed to convert resume");
  }

  return res.json();
}


