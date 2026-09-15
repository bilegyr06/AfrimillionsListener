# Afrimillions — frontend (placeholder)

The operator UI is not built yet. The backend API it will consume is served by
FastAPI at port 8000 (see `docker-compose.yml`) and documents itself at
`/docs`. Relevant payloads and endpoints:

- SMS delivery/statistics: `/report/overview`, `/stats`, `/sms/logs`
- Campaign lifecycle: `/campaign/start`, `/campaign/close`, `/campaign/current`, `/campaigns`, `/campaign/{id}`, `/campaign/{id}/customers`
- Data intake: `POST /files` (CSV upload), `GET /files`
- Operator settings: `GET /settings`, `POST /settings`
- Cycle control: `/trigger`, `/trigger/welcome`, `/trigger/inactive`, `/status`, `/cancel`
- Manual SMS: `POST /sms`

No stray copy of these endpoints should exist here; all implementation lives in
`../backend/app`.