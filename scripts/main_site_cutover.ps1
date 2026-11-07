#requires -Version 7.0
# Dot-source only. Live operations require independent review and explicit authorization.
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Invoke-MainDocker([string[]]$Arguments) {
    $PSNativeCommandUseErrorActionPreference = $false
    $output = & docker @Arguments 2>$null
    if ($LASTEXITCODE -ne 0) { throw 'Docker command failed; dependent operations aborted.' }
    return $output
}

function Read-MainJson([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { throw 'Required evidence file missing.' }
    return Get-Content -LiteralPath $Path -Raw -Encoding utf8 | ConvertFrom-Json -AsHashtable
}

function Read-MainEnvironment([string]$Text) {
    $values = @{}
    foreach ($line in ($Text -split "`n")) {
        if (-not $line.Trim() -or $line.StartsWith('#')) { continue }
        $position = $line.IndexOf('=')
        if ($position -lt 1) { throw 'Invalid environment file.' }
        $key = $line.Substring(0, $position)
        if ($values.ContainsKey($key)) { throw 'Duplicate environment key.' }
        $values[$key] = $line.Substring($position + 1).TrimEnd("`r")
    }
    return $values
}

function Assert-MainPath([string]$Actual, [string]$Expected) {
    if (-not $Actual -or [IO.Path]::GetFullPath($Actual) -ne [IO.Path]::GetFullPath($Expected)) {
        throw 'Deployment path reference mismatch.'
    }
}

function Get-MainContainer([string]$Name) {
    $items = @(Read-MainDockerJson @('inspect', $Name))
    if ($items.Count -ne 1) { throw 'Unexpected container inspection result.' }
    return $items[0]
}

function Read-MainDockerJson([string[]]$Arguments) {
    $text = Invoke-MainDocker -Arguments $Arguments
    return ($text -join "`n") | ConvertFrom-Json -AsHashtable
}

function Assert-MainIdentity($Actual, $Expected) {
    if ($Actual.Id -ne $Expected.id -or $Actual.Image -ne $Expected.image -or
        $Actual.Name -ne $Expected.name) { throw 'Unexpected original container identity.' }
}

function Get-MainSiteContext([string]$DeploymentDir, [string]$DataBackupDir) {
    $deployment = (Resolve-Path -LiteralPath $DeploymentDir).Path
    $backup = (Resolve-Path -LiteralPath $DataBackupDir).Path
    if ($deployment -eq $backup) { throw 'Deployment evidence and data backup must be separate directories.' }
    foreach ($name in @('live-runtime.env.dpapi', 'live-compose.env', 'live-guard.json',
                       'deployment.json', 'final-build/candidate-source.json')) {
        if (-not (Test-Path -LiteralPath (Join-Path $deployment $name) -PathType Leaf)) {
            throw 'Required deployment configuration missing before downtime.'
        }
    }
    $source = Read-MainJson (Join-Path $deployment 'final-build/candidate-source.json')
    $guard = Read-MainJson (Join-Path $deployment 'live-guard.json')
    $old = @(Read-MainJson (Join-Path $deployment 'deployment.json'))
    $compose = Join-Path $PSScriptRoot '../compose.main-site.yaml'
    $preflight = Join-Path $PSScriptRoot 'main_site_preflight.py'
    if ($source.approved_revision -ne '5e174e0cc38a8ff26e66406423b646c74e3c6b4f' -or
        (Get-FileHash -LiteralPath $compose).Hash -ne $source.compose_sha256 -or
        (Get-FileHash -LiteralPath $preflight).Hash -ne $source.preflight_sha256) {
        throw 'Verified deployment source or configuration changed.'
    }
    $settings = Read-MainEnvironment (Get-Content -LiteralPath (Join-Path $deployment 'live-compose.env') -Raw -Encoding utf8)
    foreach ($key in @('TASKPILOT_RUNTIME_ENV', 'TASKPILOT_ARTIFACT_ROOT', 'TASKPILOT_CREDENTIAL_ROOT')) {
        if (-not $settings.ContainsKey($key)) { throw 'Required deployment path missing.' }
    }
    Assert-MainPath $settings.TASKPILOT_RUNTIME_ENV (Join-Path $deployment 'live-runtime.env')
    Assert-MainPath $settings.TASKPILOT_ARTIFACT_ROOT $guard.artifact_root
    foreach ($key in @('TASKPILOT_ARTIFACT_ROOT', 'TASKPILOT_CREDENTIAL_ROOT')) {
        if (-not (Test-Path -LiteralPath $settings[$key] -PathType Container)) { throw 'Required bind directory missing.' }
    }
    if ($settings.TASKPILOT_API_IMAGE -ne $source.api_image -or
        $settings.TASKPILOT_WEB_IMAGE -ne $source.web_image -or
        $settings.TASKPILOT_NETWORK -ne $guard.network -or
        $settings.TASKPILOT_API_PORT -ne '8080' -or $settings.TASKPILOT_WEB_PORT -ne '5173') {
        throw 'Deployment image, network or port reference mismatch.'
    }
    foreach ($image in @($source.api_image, $source.web_image)) {
        if ($image -notmatch '^sha256:[0-9a-f]{64}$') { throw 'Immutable image ID required.' }
        $inspection = @(Read-MainDockerJson @('image', 'inspect', $image))[0]
        if ($inspection.Id -ne $image -or
            $inspection.Config.Labels['org.opencontainers.image.revision'] -ne $source.approved_revision -or
            $inspection.Config.Labels['taskpilot.main.compose.sha256'] -ne $source.compose_sha256 -or
            $inspection.Config.Labels['taskpilot.main.preflight.sha256'] -ne $source.preflight_sha256) {
            throw 'Verified image identity mismatch.'
        }
    }
    $oldApi = @($old | Where-Object name -eq '/agent-service-toolkit-agent_service-1')
    $oldWeb = @($old | Where-Object name -eq '/agent-service-toolkit-web-1')
    $oldPg = @($old | Where-Object name -eq '/agent-service-toolkit-postgres-1')
    if ($oldApi.Count -ne 1 -or $oldWeb.Count -ne 1 -or $oldPg.Count -ne 1) { throw 'Original deployment identities missing.' }
    foreach ($container in $old) { Assert-MainIdentity (Get-MainContainer $container.name) $container }
    $pg = Get-MainContainer $oldPg[0].name
    if (-not $pg.State.Running -or -not ($pg.Mounts | Where-Object {
        $_.Type -eq 'volume' -and $_.Name -eq 'agent-service-toolkit_postgres_data' -and
        $_.Destination -eq '/var/lib/postgresql/data'
    })) { throw 'Original PostgreSQL or protected volume unavailable.' }
    $artifactMount = @($oldApi[0].mounts | Where-Object Destination -eq '/app/data/geochange-artifacts')
    $credentialMount = @($oldApi[0].mounts | Where-Object Destination -eq '/privatecredentials')
    if ($artifactMount.Count -ne 1 -or $credentialMount.Count -ne 1) { throw 'Original bind evidence missing.' }
    Assert-MainPath $settings.TASKPILOT_ARTIFACT_ROOT $artifactMount[0].Source
    Assert-MainPath $settings.TASKPILOT_CREDENTIAL_ROOT $credentialMount[0].Source

    # These are exactly backup_main_site.py outputs; deployment files are never read here.
    $inventory = Read-MainJson (Join-Path $backup 'backup-inventory.json')
    if ($inventory.status -notin @('VERIFIED_ARCHIVES_RESTORE_PENDING', 'VERIFIED_RESTORE_PASS')) {
        throw 'Data backup is incomplete.'
    }
    foreach ($name in @('database.dump', 'database-baseline.json', 'artifact-references.json',
                       'deployment.json', 'container-definitions.dpapi', 'rollback-images.tar',
                       'live-artifacts.tar.gz', 'historical-artifacts.tar.gz',
                       'live-artifacts-inventory.json', 'historical-artifacts-inventory.json')) {
        $file = Join-Path $backup $name
        if (-not (Test-Path -LiteralPath $file -PathType Leaf) -or -not $inventory.files.ContainsKey($name)) {
            throw 'Required data backup output missing.'
        }
        if ((Get-Item -LiteralPath $file).Length -ne $inventory.files[$name].bytes -or
            (Get-FileHash -LiteralPath $file).Hash -ne $inventory.files[$name].sha256) {
            throw 'Data backup integrity mismatch.'
        }
    }
    $baseline = Read-MainJson (Join-Path $backup 'database-baseline.json')
    if ($baseline.revision -ne 't037_proposal_identity' -or
        $baseline.counts.users -lt $guard.protected_user_ids.Count -or
        $baseline.counts.tasks -lt $guard.min_tasks -or $baseline.counts.task_runs -lt $guard.min_task_runs) {
        throw 'Data backup schema or row-count mismatch.'
    }
    $backupContainers = @(Read-MainJson (Join-Path $backup 'deployment.json'))
    foreach ($container in $old) {
        $match = @($backupContainers | Where-Object name -eq $container.name)
        if ($match.Count -ne 1 -or $match[0].id -ne $container.id -or $match[0].image -ne $container.image) {
            throw 'Data backup belongs to another deployment.'
        }
    }
    try {
        $secure = ConvertTo-SecureString (Get-Content -LiteralPath (Join-Path $deployment 'live-runtime.env.dpapi') -Raw).Trim()
        $plaintext = [Net.NetworkCredential]::new('', $secure).Password
        $runtime = Read-MainEnvironment $plaintext
        $expected = @{
            TASKPILOT_EXPECTED_DATABASE = $guard.database
            TASKPILOT_EXPECTED_SYSTEM_ID = $guard.system_identifier
            TASKPILOT_EXPECTED_USER_IDS_SHA256 = $guard.user_ids_sha256
            TASKPILOT_PROTECTED_USER_IDS = ($guard.protected_user_ids -join ',')
            TASKPILOT_MIN_TASKS = [string]$guard.min_tasks
            TASKPILOT_MIN_TASK_RUNS = [string]$guard.min_task_runs
        }
        foreach ($key in $expected.Keys) {
            if (-not $runtime.ContainsKey($key) -or $runtime[$key] -cne $expected[$key]) { throw 'Runtime guard mismatch.' }
        }
        $url = [Uri]$runtime.TASKPILOT_DATABASE_URL
        if ($url.Scheme -notin @('postgresql', 'postgresql+psycopg') -or $url.Host -ne 'postgres' -or
            $url.Port -ne 5432 -or $url.AbsolutePath -ne ('/' + $guard.database)) { throw 'Runtime database reference mismatch.' }
        if (Test-Path -LiteralPath $settings.TASKPILOT_RUNTIME_ENV) {
            if ((Get-Content -LiteralPath $settings.TASKPILOT_RUNTIME_ENV -Raw -Encoding utf8) -cne $plaintext) {
                throw 'Existing runtime environment differs from verified encrypted evidence.'
            }
        } else {
            [IO.File]::WriteAllText($settings.TASKPILOT_RUNTIME_ENV, $plaintext, [Text.UTF8Encoding]::new($false))
        }
        $composeArgs = @('compose', '--env-file', (Join-Path $deployment 'live-compose.env'),
                         '-p', 'taskpilot-main-v04', '-f', [IO.Path]::GetFullPath($compose))
        Invoke-MainDocker -Arguments ($composeArgs + @('config', '--quiet')) | Out-Null
        $resolved = Read-MainDockerJson ($composeArgs + @('config', '--format', 'json'))
        if ((($resolved.services.Keys | Sort-Object) -join ',') -ne 'api,web' -or
            $resolved.services.api.image -ne $source.api_image -or $resolved.services.web.image -ne $source.web_image -or
            -not $resolved.networks.existing.external -or $resolved.networks.existing.name -ne $guard.network) {
            throw 'Resolved Compose topology mismatch.'
        }
        foreach ($key in $runtime.Keys) {
            if ($resolved.services.api.environment[$key] -cne $runtime[$key]) { throw 'Resolved runtime environment mismatch.' }
        }
        foreach ($pair in @(@('api', '8080', '8080'), @('web', '5173', '5173'))) {
            $ports = @($resolved.services[$pair[0]].ports)
            if ($ports.Count -ne 1 -or $ports[0].target -ne $pair[1] -or
                $ports[0].published -ne $pair[2] -or $ports[0].host_ip -ne '127.0.0.1') { throw 'Resolved port mismatch.' }
        }
        foreach ($pair in @(@('/app/data/geochange-artifacts', $settings.TASKPILOT_ARTIFACT_ROOT),
                            @('/privatecredentials', $settings.TASKPILOT_CREDENTIAL_ROOT))) {
            $mount = @($resolved.services.api.volumes | Where-Object target -eq $pair[0])
            if ($mount.Count -ne 1 -or $mount[0].type -ne 'bind' -or $mount[0].bind.create_host_path) { throw 'Resolved bind mismatch.' }
            Assert-MainPath $mount[0].source $pair[1]
        }
    } catch { throw 'Runtime environment or Compose validation failed; sensitive details suppressed.' }
    finally { Remove-Variable plaintext, runtime, secure -ErrorAction SilentlyContinue }
    return @{ ComposeArgs = $composeArgs; OldApi = $oldApi[0]; OldWeb = $oldWeb[0]; Source = $source }
}

function Get-MainSiteNewContainers($Context) {
    $ids = @(Invoke-MainDocker -Arguments @('ps', '-aq', '--filter', 'label=com.docker.compose.project=taskpilot-main-v04'))
    $containers = @()
    foreach ($id in $ids) {
        if (-not $id) { continue }
        $container = Get-MainContainer $id
        $service = $container.Config.Labels['com.docker.compose.service']
        if ($service -notin @('api', 'web') -or $container.Name -ne "/taskpilot-main-v04-$service-1" -or
            $container.Config.Labels['com.docker.compose.project'] -ne 'taskpilot-main-v04' -or
            $container.Image -ne $Context.Source[($service + '_image')]) { throw 'Unexpected new container identity.' }
        $containers += $container
    }
    return $containers
}

function Assert-MainStopped($Container) {
    if ($Container.State.Running -or $Container.State.Restarting -or $Container.State.Paused -or
        $Container.State.Status -notin @('exited', 'created')) { throw 'Container is not fully stopped.' }
}

function Wait-MainContainerReady($Expected, [int]$Port, [ValidateRange(1, 300)][int]$TimeoutSeconds = 30) {
    $timer = [Diagnostics.Stopwatch]::StartNew()
    while ($timer.Elapsed.TotalSeconds -lt $TimeoutSeconds) {
        $actual = Get-MainContainer $Expected.name
        Assert-MainIdentity $actual $Expected
        if (-not $actual.State.Running -or $actual.State.Restarting -or $actual.State.Paused) { throw 'Intended container is not running.' }
        $PSNativeCommandUseErrorActionPreference = $false
        if ($Port -eq 8080) {
            & docker exec $Expected.id python -c "from urllib.request import urlopen; assert urlopen('http://127.0.0.1:8080/health/ready', timeout=1).status == 200" 2>$null | Out-Null
            $readyExit = $LASTEXITCODE
        } else {
            & docker exec $Expected.id wget -q --timeout=1 -O /dev/null http://127.0.0.1:5173/ 2>$null | Out-Null
            $readyExit = $LASTEXITCODE
        }
        if ($readyExit -eq 0 -and $actual.State.Health.Status -eq 'healthy') {
            $confirmed = Get-MainContainer $Expected.name
            Assert-MainIdentity $confirmed $Expected
            if (-not $confirmed.State.Running -or $confirmed.State.Restarting -or
                $confirmed.State.Paused -or $confirmed.State.Health.Status -ne 'healthy') {
                throw 'Intended container changed state during readiness check.'
            }
            return
        }
        Start-Sleep -Milliseconds 200
    }
    throw 'Intended container readiness timed out; dependent start aborted.'
}

function Stop-MainSiteOriginal([string]$DeploymentDir, [string]$DataBackupDir) {
    $context = Get-MainSiteContext $DeploymentDir $DataBackupDir
    if (@(Get-MainSiteNewContainers $context | Where-Object { $_.State.Running -or $_.State.Restarting -or $_.State.Paused }).Count) {
        throw 'New deployment already active before original shutdown.'
    }
    foreach ($old in @($context.OldWeb, $context.OldApi)) {
        Invoke-MainDocker -Arguments @('stop', $old.id) | Out-Null
        $actual = Get-MainContainer $old.name
        Assert-MainIdentity $actual $old
        Assert-MainStopped $actual
    }
}

function Start-MainSiteCandidate([string]$DeploymentDir, [string]$DataBackupDir, [ValidateRange(1, 300)][int]$TimeoutSeconds = 30) {
    $context = Get-MainSiteContext $DeploymentDir $DataBackupDir
    foreach ($old in @($context.OldWeb, $context.OldApi)) {
        $actual = Get-MainContainer $old.name
        Assert-MainIdentity $actual $old
        Assert-MainStopped $actual
    }
    Get-MainSiteNewContainers $context | Out-Null
    Invoke-MainDocker -Arguments ($context.ComposeArgs + @('up', '-d', '--no-build', '--pull', 'never')) | Out-Null
    $new = @(Get-MainSiteNewContainers $context)
    if ($new.Count -ne 2) { throw 'Both intended candidate containers are required.' }
    foreach ($service in @('api', 'web')) {
        $container = @($new | Where-Object { $_.Config.Labels['com.docker.compose.service'] -eq $service })
        if ($container.Count -ne 1) { throw 'Candidate identity is ambiguous.' }
        $expected = @{ id = $container[0].Id; image = $container[0].Image; name = $container[0].Name }
        Wait-MainContainerReady $expected $(if ($service -eq 'api') { 8080 } else { 5173 }) $TimeoutSeconds
    }
}

function Restore-MainSiteOriginal([string]$DeploymentDir, [string]$DataBackupDir, [ValidateRange(1, 300)][int]$TimeoutSeconds = 30) {
    $context = Get-MainSiteContext $DeploymentDir $DataBackupDir
    Get-MainSiteNewContainers $context | Out-Null
    Invoke-MainDocker -Arguments ($context.ComposeArgs + @('stop', 'web', 'api')) | Out-Null
    foreach ($container in @(Get-MainSiteNewContainers $context)) { Assert-MainStopped $container }
    Assert-MainIdentity (Get-MainContainer $context.OldApi.name) $context.OldApi
    Invoke-MainDocker -Arguments @('start', $context.OldApi.id) | Out-Null
    Wait-MainContainerReady $context.OldApi 8080 $TimeoutSeconds
    Assert-MainIdentity (Get-MainContainer $context.OldWeb.name) $context.OldWeb
    Invoke-MainDocker -Arguments @('start', $context.OldWeb.id) | Out-Null
    Wait-MainContainerReady $context.OldWeb 5173 $TimeoutSeconds
}
