function ConvertTo-UtcCutoff {
    param([Parameter(Mandatory = $true)][string]$Value)

    if ($Value -notmatch '(?:Z|[+-]\d{2}:\d{2})$') {
        throw "-SinceTime must include an explicit UTC offset, for example 2026-09-01T12:34:56Z"
    }

    $parsed = [datetimeoffset]::MinValue
    $styles = [System.Globalization.DateTimeStyles]::AllowWhiteSpaces
    if (-not [datetimeoffset]::TryParse(
            $Value,
            [System.Globalization.CultureInfo]::InvariantCulture,
            $styles,
            [ref]$parsed
        )) {
        throw "Invalid -SinceTime value: $Value"
    }
    return $parsed.ToUniversalTime()
}

function Select-ForcePushEvent {
    param(
        [Parameter(Mandatory = $true)][string]$SinceCommit,
        [Parameter(Mandatory = $true)][AllowEmptyCollection()][string[]]$EventLines
    )

    if ($SinceCommit -notmatch '^[0-9a-fA-F]{7,40}$') {
        throw "-SinceCommit must be a 7-40 character hexadecimal commit id"
    }

    $matches = @()
    foreach ($line in $EventLines) {
        if (-not $line) { continue }
        $fields = $line.Trim() -split "`t", 3
        if ($fields.Count -lt 3 -or -not $fields[0] -or -not $fields[2]) { continue }
        if ($fields[0].StartsWith($SinceCommit, [System.StringComparison]::OrdinalIgnoreCase)) {
            $matches += [pscustomobject]@{
                BeforeCommit = $fields[0]
                AfterCommit = $fields[1]
                CreatedAt = ConvertTo-UtcCutoff $fields[2]
            }
        }
    }

    if ($matches.Count -eq 0) {
        throw "No HeadRefForcePushedEvent matches -SinceCommit $SinceCommit; pass -SinceTime explicitly"
    }
    if ($matches.Count -gt 1) {
        throw "Multiple HeadRefForcePushedEvent values match -SinceCommit $SinceCommit; pass -SinceTime explicitly"
    }
    return $matches[0]
}

function Test-TimestampAfterCutoff {
    param(
        [string]$Timestamp,
        [AllowNull()][object]$Cutoff
    )

    if ($null -eq $Cutoff) { return $true }
    if (-not $Timestamp) { return $false }
    try {
        $candidate = ConvertTo-UtcCutoff $Timestamp
    } catch {
        return $false
    }
    return $candidate -gt ([datetimeoffset]$Cutoff)
}

function Test-ReviewCoversHead {
    param(
        [AllowNull()][object]$Review,
        [Parameter(Mandatory = $true)][string]$HeadSha,
        [AllowNull()][object]$Cutoff
    )

    return $null -ne $Review -and
        $Review.commit_id -eq $HeadSha -and
        (Test-TimestampAfterCutoff -Timestamp $Review.submitted_at -Cutoff $Cutoff)
}

function Test-StatusCompletesHead {
    param(
        [AllowNull()][object]$Status,
        [Parameter(Mandatory = $true)][string]$HeadSha,
        [AllowNull()][object]$Cutoff
    )

    return $null -ne $Status -and
        $Status.sha -eq $HeadSha -and
        $Status.state -eq "success" -and
        (Test-TimestampAfterCutoff -Timestamp $Status.created_at -Cutoff $Cutoff)
}
