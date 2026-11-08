# Final Project Criteria

There are 12 criteria worth 24 points. You need 10 points to pass.

You do not have to score full marks on every criterion.

### Problem Description

- The problem is not described. (0 points)
- The problem is described briefly, but it is unclear what the system does or what functionality is expected. (1 point)
- The README clearly describes the problem, the system functionality, and expected behavior. (2 points)

### Product Specification

- No written specs. (0 points)
- A spec exists, but no public task tracker. (1 point)
- A specification exists and there's a publicly accessible task tracker. The tasks define acceptance criteria and there's evidence that the agents used the tracker. (2 points)

### Agent Team Specification

- No agent team described. (0 points)
- Roles or agents are mentioned, but responsibilities are unclear. (1 point)
- The project defines the agent team and process of how they work together. (2 points)

### Context Management

- No project docs. (0 points)
- An AGENTS.md (or equivalent) exists and describes the project. (1 point)
- AGENTS.md exists and it also points to modular docs (process, teams, etc.). (2 points)

### Application

- No working application, or frontend, backend, or persistent storage is missing. (0 points)
- Frontend, backend, and database exist but are not fully connected. (1 point)
- Full-stack application works end to end: frontend talks to backend, data persists in a database. Application runs locally with a single command. (2 points)

### Testing

- No tests, or the tests do not run. (0 points)
- Only unit tests or only integration tests exist. (1 point)
- Both unit and integration tests exist, run with a single command, and pass. (2 points)

### Containerization

- No containerization. (0 points)
- Dockerfiles exist, but running the full system requires manual steps. (1 point)
- The full system, including the database, runs via Docker or Docker Compose and starts with a single command. (2 points)

### Deployment

- Application is not deployed. (0 points)
- Deployment steps are described, but no working deployment is shown. (1 point)
- The application is deployed and reachable at a public URL, or there's a clear demo with the deployment. (2 points)

### CI/CD

- No CI/CD pipeline. (0 points)
- CI runs the tests automatically on every pull request. (1 point)
- CI runs tests and CD automatically deploys. (2 points)
- CI runs tests, CD deploys to dev, and there's a manual deploy to production. (3 points)

### Observability

- No telemetry. (0 points)
- The app emits logs, but there are no metrics or traces, or the signals cannot be correlated to a single request. (1 point)
- A key user journey is instrumented with metrics, traces, and structured logs that share a trace ID; no customer data or credentials are sent to telemetry; and the signals are collected, stored, and viewable in a UI. (2 points)

### Alerting on User Impact

- No alerts. (0 points)
- At least one alert exists. (1 point)

### On-Call Agent

- No on-call agent. (0 points)
- An agent wakes up on an alert and fixes the issue. (2 points)
