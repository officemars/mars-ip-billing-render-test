# MARS-IP Billing - Render/Neon test

## Repository name
`mars-ip-billing-render-test`

## Required private assets
Before pushing to GitHub, copy these files into `assets/` using the exact names:

- `MARS-IP_Unified_Billing_System_FINAL 1.xlsx`
- `MARSIP reference invoice FR.docx`
- `MARSIP reference invoice DE.docx`
- `MARSIP reference invoice EN.docx`

The app imports Clients, Matters, Contacts, and Users & Timekeepers from the workbook at startup. Re-import is available to administrators.

## Render Blueprint
Deploy this repository with Render **New > Blueprint**. Render reads `render.yaml` and asks for `DATABASE_URL` and `ADMIN_PASSWORD`.

## Security
Never commit `.env`, the Neon connection string, production passwords, or exported client data to a public repository. Keep the GitHub repository private.
