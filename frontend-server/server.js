import express from "express";
import { GoogleAuth } from "google-auth-library";

const app = express();
app.use(express.json());

const BACKEND_URL = "https://resume-backend-v4-1069915675190.us-central1.run.app/";

app.post("/generate", async (req, res) => {
  try {
    const auth = new GoogleAuth();
    const client = await auth.getIdTokenClient(BACKEND_URL);

    const response = await client.request({
      url: `${BACKEND_URL}/generate`,
      method: "POST",
      data: req.body,
      headers: {
        "x-api-key": "mysecret22308"
      }
    });

    res.json(response.data);

  } catch (err) {
    console.error(err);
    res.status(500).send("Error calling backend");
  }
});

app.post("/convert", async (req, res) => {
  try {
    const auth = new GoogleAuth();
    const client = await auth.getIdTokenClient(BACKEND_URL);

    const response = await client.request({
      url: `${BACKEND_URL}/convert`,
      method: "POST",
      data: req.body,
      headers: {
        "x-api-key": "mysecret22308"
      }
    });

    res.json(response.data);

  } catch (err) {
    console.error(err);
    res.status(500).send("Error calling backend");
  }
});

app.listen(8080, () => console.log("Frontend server running"));