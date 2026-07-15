# Problems Solved

This project didn't go smoothly, and I think that's worth documenting honestly rather than presenting a polished "it just worked" narrative. Below are the real issues I hit, in the order I hit them, and how I diagnosed and fixed each one.

## 1. The hardware couldn't run the stack at all

**Constraint:** 8 GB of installed RAM, but one memory stick had failed — only 4 GB usable. A struggling SSD on top of that. The intended stack (Wazuh + TheHive + Cortex + MISP + a local LLM) typically recommends 32 GB+ combined.

**Decision:** Rather than scale the project down, I moved the entire stack to a free-tier Google Cloud VM (4 vCPU, 16 GB RAM) and used my laptop purely as a client and, later, an attack source. This meant learning cloud VM provisioning, firewall rules, and SSH-based remote administration from scratch as part of the project — not something I'd originally planned to need.

## 2. Docker Compose dependency race conditions

**Symptom:** Containers like `misp-core` or `cortex` would repeatedly show `Restarting` or fail health checks, even though their dependencies (database, Elasticsearch) appeared to be running.

**Diagnosis:** `docker compose up -d` doesn't reliably wait for a dependency to be *functionally* ready — only for its container to have started. A database container can report "started" seconds before it's actually able to accept queries.

**Fix:** Brought up dependencies first, waited explicitly, then started the dependent service in a second command:
```bash
docker compose up -d db redis mail misp-modules
sleep 30
docker compose up -d misp-core
```

## 3. Disk space silently hit 100%, causing cascading failures across three tools

**Symptom:** MISP wouldn't accept logins, TheHive crashed on startup with an Elasticsearch index error, and general commands like `mkdir` started failing with cryptic errors.

**Diagnosis:** `df -h` showed the root filesystem at 100%. The root cause was accumulated Docker image layers and logs from repeated container rebuilds during earlier debugging.

**Fix:** Resized the underlying GCP disk from 50 GB to 100 GB, then grew the partition and filesystem live, without downtime:
```bash
sudo growpart /dev/sda 1
sudo resize2fs /dev/sda1
```

## 4. A single wiped volume broke two services at once

**Symptom:** After clearing Cortex's Elasticsearch data to fix a stuck admin account, TheHive started crash-looping with `index_not_found_exception: no such index [thehive_global]`.

**Diagnosis:** TheHive and Cortex were sharing the same Elasticsearch container. Wiping it to fix one service's stuck state left the other service's Cassandra database believing it was already initialized, while its search index was gone — a state mismatch, not a crash.

**Fix:** Wiped *all* related volumes (Cassandra + Elasticsearch) together so both re-initialized from a consistent clean state, rather than trying to patch the mismatch.

## 5. TheHive's permission model isn't what it looks like

**Symptom:** An API call to create a case returned `403 Forbidden: you don't have the permission manageCase/create` — using an account that was, from the UI, clearly an "admin."

**Diagnosis:** In TheHive 5.x, the built-in platform "admin" profile is scoped to *platform management* (users, organisations, config) — it deliberately does **not** include case-handling rights. Case permissions live on a completely separate `analyst` / `org-admin` profile, and critically, that profile is assigned **per organisation membership**, not globally on the user account.

**Fix:** Created a dedicated organisation (`soclab`) and a service-type user inside it, explicitly assigned the `analyst` profile at the organisation-membership level, and generated its API key from there.

## 6. A hardcoded port in MISP's redirect logic

**Symptom:** Every internal "Home" or "Dashboard" link in MISP silently dropped the custom port I'd configured (`:8443`), redirecting to the bare domain — which then hit whatever else was listening on the default port 443 (in this case, Wazuh's dashboard).

**Diagnosis:** Confirmed via `curl -I` that MISP's own redirect `Location` header omitted the port, regardless of three different places I'd set the base URL (`.env`, `config.php` directly, and MISP's own `cake Admin setSetting` CLI tool) — all three reverted after every container restart.

**Fix:** Rather than continuing to fight a packaging-level bug, I re-architected the port assignment: moved MISP to the default ports (80/443), and moved Wazuh's dashboard to a non-default port (8444) instead. Since MISP's redirects default to port 443 anyway, putting MISP *on* 443 made the bug irrelevant.

## 7. A convincing red herring: browser autocomplete, not a server bug

**Symptom:** After the port swap above, login still intermittently redirected to a stale `:8443` URL that no longer even existed.

**Diagnosis:** Spent time re-checking server-side config before testing in a different browser entirely (Brave). It worked immediately, cleanly, every time — proving the issue was Chrome's address bar autocomplete silently completing the URL with a cached, outdated port before the page even loaded.

**Lesson:** When a UI bug is inconsistent across attempts but consistent within one browser, check the browser before the server.

## 8. `sudo` reporting "a password is required" despite passwordless sudo being configured

**Symptom:** `sudo /var/ossec/bin/agent-control -l` prompted for a password, even though `sudo -l` confirmed `NOPASSWD: ALL` for my user.

**Diagnosis:** The command name had a typo — `agent-control` (hyphen) instead of the real binary `agent_control` (underscore), which in that Wazuh version didn't even exist under either name (it had been replaced by the dashboard API). `sudo`'s error message for "command not found while also needing to re-auth" is misleading and reads like a permissions problem.

**Fix:** Verified the actual binary with `ls`, found the real management interface had moved to the Wazuh Dashboard UI in this version.

## 9. Wazuh wasn't monitoring the log file that actually contained the attack

**Symptom:** A real SSH brute-force attack (verified present in `/var/log/auth.log`) produced zero alerts in Wazuh.

**Diagnosis:** The Wazuh agent's `ossec.conf` had `<localfile>` entries for `dpkg.log`, `active-responses.log`, and `journald` — but no entry for `/var/log/auth.log` at all.

**Fix:** Added the missing `<localfile>` block, restarted the agent, and confirmed detection with a live re-test — this time the attack correctly triggered rule `5763` (level 10, MITRE T1110 - Brute Force).

## 10. Alert-noise tuning

**Symptom:** After lowering the alert threshold for testing, the AI pipeline started firing on routine background events (Wazuh's periodic `rootcheck`/compliance scans) every couple of minutes, flooding TheHive with irrelevant cases.

**Fix:** Rather than filtering by a broad severity level or rule group (which still matched noisy single-event rules), scoped the integration to one specific, meaningful rule ID — `5763`, the correlated brute-force detection — so the pipeline only fires on genuinely significant, multi-event patterns.

---

**Takeaway:** most of these issues weren't fixed by finding a magic command online — they were fixed by reading actual error messages carefully, checking logs at the right layer (container vs. host vs. application), and being willing to re-architect (port swap) rather than endlessly patch a packaging quirk.
