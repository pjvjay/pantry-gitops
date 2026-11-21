-- Argo CD health check for CloudNativePG `Backup` (postgresql.cnpg.io).
--
-- Installed by pantry-infra into the HUB argocd-cm as
--   resource.customizations.health.postgresql.cnpg.io_Backup
-- (docs/handoffs/pantry-infra.md §11). Argo CD waits on a non-Job hook's
-- HEALTH, so without this the prod pre-promotion backup hook
-- (stages/prod/infra/db-ns/pre-promotion-backup.yaml) would pass instantly
-- and gate nothing. walArchivingFailing is Degraded: the backup cannot
-- complete, and Progressing would hang the prod promotion.
--
-- Tested: scripts/test_platform_lua.py
hs = {}
local phase = obj.status ~= nil and obj.status.phase or nil
if phase == "completed" then
  hs.status = "Healthy"
  hs.message = "Backup completed"
elseif phase == "failed" or phase == "walArchivingFailing" then
  hs.status = "Degraded"
  hs.message = (obj.status.error or ("Backup " .. phase))
else
  hs.status = "Progressing"
  hs.message = "Backup " .. (phase or "pending")
end
return hs
