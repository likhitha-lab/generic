# Regression sample resumes

Drop real, anonymized resumes here (`.docx` or `.pdf`) to expand the live-Gemini
regression suite (`tests/test_regression_sample_resumes.py`, opt-in only via
`pytest -m live_gemini` - see that file's own docstring for what it checks and why
it's excluded from the default test run).

The suite already picks up the two existing samples directly in `reference_docs/`
(`Dataflix_Vinay Kumar_Informatica.docx`, `Pradeep Kumar Shetty6.docx`) plus
anything added here or in a subfolder here - no test-code change is needed to add
a new sample, just drop the file in.

For full coverage across the resume formats/roles this platform targets, aim for
at least one real sample for each of:

- Cloud Engineer
- AI Engineer / Machine Learning Engineer
- Project Manager / Product Manager
- SAP Consultant
- Informatica Developer (already covered)
- React Developer
- Java Developer / .NET Developer
- DevOps Engineer
- Data Engineer
- Supply Chain Consultant
- Business Analyst
- Content Writer
- Mechanical Engineer / Civil Engineer
- A fresher (little/no work experience)
- A senior management professional

Anonymize real names/contact details/employer names before adding a file here if
the source resume is a real candidate's, not a synthetic/test document - this
folder is committed to the repository.
