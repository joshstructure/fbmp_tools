<#
.SYNOPSIS
    Creates FB-MultiPier input files with the applied loading mirrored across the
    pile cap centerlines, keeping the (as-built) foundation exactly as modeled.

.DESCRIPTION
    Instead of mirroring the pile grid to cover all angles of attack, this tool keeps
    the base .in file's foundation and rewrites the directional loading (by default
    load types WS*, WL*, BR*, WA* and TU*, see -MirrorTypes) so that each of those loads
    acts at the mirror-image location with mirrored components.  Gravity and all
    other load types stay exactly as in the base model.

        Mirror X  (flip X coordinates):  Fx, My, Mz change sign
        Mirror Y  (flip Y coordinates):  Fy, Mx, Mz change sign
        Mirror XY (both):                Fx, Fy, Mx, My change sign

    The node-to-coordinate map is read from the FB-MultiPier .out file of a run of the
    base model (the "NODAL INFORMATION" section).  Each loaded node is moved to the
    nearest node of the same kind (pile / cap / pier / bearing) at the mirrored
    position.  Bearing-row loads (LOADBP) are mirrored by bearing index and L/R side
    and need no node lookup.

    Sections LOAD and LOADBP are rewritten.  Everything else (pile grid, MISSING, CAP,
    REMOVE, SOIL, WIND, COMBINATION ...) is copied unchanged, except the optional
    p-multiplier replacement (see -PMultX / -PMultY / -PMultFromFile).

    FB-MultiPier applies p-multipliers "from lead to trail row based on the actual
    displacement", so the lists in the .in are relative (trail -> lead) and are NOT
    reordered by this tool.  If the values were calibrated to the as-built spacing for
    a particular direction, supply the list calibrated for the mirrored direction.

.PARAMETER InFile
    Base FB-MultiPier input file (.in) with the as-built foundation and the loads.
.PARAMETER OutFile
    FB-MultiPier output file (.out) from a run of the base model (same foundation).
.PARAMETER Mirror
    X, Y, XY or All (default All = write the three files).
.PARAMETER OutputDir
    Folder for the generated files (default: folder of InFile).
.PARAMETER Tolerance
    Distance (inches) above which a node match is flagged CHECK in the report. Default 24.
.PARAMETER CenterX / CenterY
    Mirror plane positions (inches, FB-MultiPier global coordinates).  Default: the
    midpoint of the pile-cap node extents read from the .out file.
.PARAMETER PMultX / PMultY
    Comma-separated p-multiplier lists (trail-to-lead, same order as the .in) to write
    into the generated files.  PMultX is used for the X and XY files, PMultY for Y and XY.
.PARAMETER PMultXFile / PMultYFile
    Other .in files (e.g. the existing mirrored-foundation models calibrated for that
    direction) whose X list (PMultXFile) or Y list (PMultYFile) is copied into the
    generated files.  -PMultX / -PMultY override these.
.PARAMETER MirrorTypes
    Comma-separated load type patterns (the T= value on each load line) that are
    mirrored.  Default "WS*,WL*,BR*,WA*,TU*" = wind on structure, wind on live load,
    braking, water and uniform temperature.  Lines of any other type (DC, CR, SH,
    LL, IM, CE ...) are copied unchanged.  Use "*" to mirror every load.
.PARAMETER TitleSuffix
    Text appended to the last PROBLEM line of each generated file
    (default " - Mirror <dir> WS WL BR WA TU", i.e. the mirrored types).

.EXAMPLE
    .\FBMP-MirrorLoads.ps1 -InFile "Pier 7_AASHTO_STR_1.in" -OutFile "Pier 7_AASHTO_STR_1.out"

.EXAMPLE
    .\FBMP-MirrorLoads.ps1 -InFile base.in -OutFile base.out -Mirror X -PMultX "0.7,0.7,0.735,...,0.9"
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)] [string] $InFile,
    [Parameter(Mandatory = $true)] [string] $OutFile,
    [ValidateSet('X', 'Y', 'XY', 'All')] [string] $Mirror = 'All',
    [string] $OutputDir,
    [double] $Tolerance = 24.0,
    [Nullable[double]] $CenterX,
    [Nullable[double]] $CenterY,
    [string] $PMultX,
    [string] $PMultY,
    [string] $PMultXFile,
    [string] $PMultYFile,
    [string] $MirrorTypes = 'WS*,WL*,BR*,WA*,TU*',
    [string] $TitleSuffix
)

$ErrorActionPreference = 'Stop'
$inv = [Globalization.CultureInfo]::InvariantCulture

function ToDouble([string]$s) { return [double]::Parse($s, $inv) }
function Fmt([double]$v, [int]$d = 3) { return $v.ToString("F$d", $inv) }

# Negate a number written as text while keeping its original formatting (0 stays 0).
function NegText([string]$s) {
    $v = 0.0
    if ([double]::TryParse($s, [Globalization.NumberStyles]::Float, $inv, [ref]$v)) {
        if ($v -eq 0) { return $s }
    }
    if ($s.StartsWith('-')) { return $s.Substring(1) }
    if ($s.StartsWith('+')) { return '-' + $s.Substring(1) }
    return '-' + $s
}

# ------------------------------------------------------------------------------------
# 1. Read node coordinates (and a few model facts) from the .out file
# ------------------------------------------------------------------------------------
function Read-OutNodes([string]$path) {
    $nodes = @{}
    $info = @{ Bearings = $null; NPX = $null; NPY = $null }
    $reader = New-Object IO.StreamReader($path)
    try {
        $inNodal = $false; $group = $null; $pileNo = 0; $firstInSection = $false
        $rowRx = [regex]'^\s*(\d+)\s+([AN])\s+([AN])\s+([AN])\s+([AN])\s+([AN])\s+([AN])\s+(-?\d+\.?\d*(?:[eE][+-]?\d+)?)\s+(-?\d+\.?\d*(?:[eE][+-]?\d+)?)\s+(-?\d+\.?\d*(?:[eE][+-]?\d+)?)\s*$'
        while ($null -ne ($line = $reader.ReadLine())) {
            if (-not $inNodal) {
                if ($line -match 'Number of Bearing Locations Specified\s*=\s*(\d+)') { $info.Bearings = [int]$Matches[1] }
                elseif ($line -match 'Number of Grid Points in Xp direction.*=\s*(\d+)') { $info.NPX = [int]$Matches[1] }
                elseif ($line -match 'Number of Grid Points in Yp direction.*=\s*(\d+)') { $info.NPY = [int]$Matches[1] }
                elseif ($line -match 'NODAL INFORMATION') { $inNodal = $true }
                continue
            }
            if ($line -match '^\s*Nodal Coordinates for (.+?)\s*$') {
                $name = $Matches[1]
                if ($name -match '^Pile #\s*(\d+)') { $group = 'pile'; $pileNo = [int]$Matches[1] }
                elseif ($name -match 'Pile Cap') { $group = 'cap'; $pileNo = 0 }
                elseif ($name -match 'Substructure') { $group = 'pier'; $pileNo = 0 }
                elseif ($name -match 'Left Bearing') { $group = 'bearingL'; $pileNo = 0 }
                elseif ($name -match 'Right Bearing') { $group = 'bearingR'; $pileNo = 0 }
                else { $group = $name; $pileNo = 0 }
                $firstInSection = $true
                continue
            }
            if ($line -match '^\s*\*{20,}\s*$' -and $nodes.Count -gt 0) { break }   # next banner: nodal section is over
            $m = $rowRx.Match($line)
            if ($m.Success) {
                $id = [int]$m.Groups[1].Value
                if (-not $nodes.ContainsKey($id)) {
                    $nodes[$id] = [pscustomobject]@{
                        Id = $id; X = (ToDouble $m.Groups[8].Value); Y = (ToDouble $m.Groups[9].Value); Z = (ToDouble $m.Groups[10].Value)
                        Group = $group; Active = ($m.Groups[2].Value -eq 'A'); Pile = $pileNo; PileHead = ($group -eq 'pile' -and $firstInSection)
                    }
                }
                $firstInSection = $false
            }
        }
    } finally { $reader.Close() }
    if ($nodes.Count -eq 0) { throw "No nodal coordinates found in '$path'. Make sure it is the FB-MultiPier .out file of the base model." }
    return @{ Nodes = $nodes; Info = $info }
}

# ------------------------------------------------------------------------------------
# 2. Read the .in file and tag every line with its section
# ------------------------------------------------------------------------------------
function Read-InSections([string[]]$lines) {
    $sec = New-Object string[] $lines.Count
    $cur = $null
    for ($i = 0; $i -lt $lines.Count; $i++) {
        $l = $lines[$i]
        if ($null -eq $cur) {
            if ($l -match '^[A-Z_]+\s*$') { $cur = $l.Trim(); $sec[$i] = "$cur#HEADER" } else { $sec[$i] = '' }
        } else {
            if ($l.Trim() -eq ':') { $sec[$i] = "$cur#END"; $cur = $null } else { $sec[$i] = $cur }
        }
    }
    return $sec
}

# Collect the numeric tokens of the PILE section that follow the "NPX, NPY" line.
function Get-PileGridBlock([string[]]$lines, [string[]]$sec) {
    $start = -1
    for ($i = 0; $i -lt $lines.Count; $i++) {
        if ($sec[$i] -eq 'PILE' -and $lines[$i] -match 'NPX,\s*NPY') { $start = $i; break }
    }
    if ($start -lt 0) { return $null }
    $hdr = ($lines[$start] -split '\s+') | Where-Object { $_ -ne '' }
    $npx = [int]$hdr[0]; $npy = [int]$hdr[1]
    $tokens = New-Object System.Collections.Generic.List[string]
    $end = $start
    $nSp = ($npx - 1) + ($npy - 1)
    $multStart = -1          # first line holding p-multiplier values (-1 if the spacing list does not end on a line break)
    for ($i = $start + 1; $i -lt $lines.Count; $i++) {
        if ($sec[$i] -ne 'PILE') { break }
        $t = ($lines[$i] -replace '\\', '') -split '\s+' | Where-Object { $_ -ne '' }
        if ($t.Count -eq 0) { break }
        $isNum = $true; foreach ($x in $t) { $v = 0.0; if (-not [double]::TryParse($x, [Globalization.NumberStyles]::Float, $inv, [ref]$v)) { $isNum = $false; break } }
        if (-not $isNum) { break }
        $before = $tokens.Count
        foreach ($x in $t) { $tokens.Add($x) }
        if ($before -eq $nSp -and $multStart -lt 0) { $multStart = $i }
        $end = $i
    }
    return @{ HeaderLine = $start; LastLine = $end; NPX = $npx; NPY = $npy; Tokens = $tokens; MultStart = $multStart }
}

function Format-NumberLines([string[]]$values, [int]$perLine, [bool]$continueAfter) {
    $out = @()
    for ($i = 0; $i -lt $values.Count; $i += $perLine) {
        $chunk = $values[$i..([Math]::Min($i + $perLine, $values.Count) - 1)]
        $last = ($i + $perLine -ge $values.Count)
        $txt = ($chunk -join ' ') + ' '
        if (-not $last -or $continueAfter) { $txt += '\' }
        $out += $txt
    }
    return $out
}

# ------------------------------------------------------------------------------------
# 3. Mirroring
# ------------------------------------------------------------------------------------
function Get-FlipMask([string]$dir) {
    # index: 0=Fx 1=Fy 2=Fz 3=Mx 4=My 5=Mz
    switch ($dir) {
        'X'  { return @($true, $false, $false, $false, $true, $true) }
        'Y'  { return @($false, $true, $false, $true, $false, $true) }
        'XY' { return @($true, $true, $false, $true, $true, $false) }
    }
}

function Find-MirrorNode($node, [string]$dir, $cands, [double]$cx, [double]$cy) {
    $tx = $node.X; $ty = $node.Y; $tz = $node.Z
    if ($dir -like '*X*') { $tx = 2 * $cx - $tx }
    if ($dir -like '*Y*') { $ty = 2 * $cy - $ty }
    $best = $null; $bestD = [double]::MaxValue
    foreach ($c in $cands) {
        $dx = $c.X - $tx; $dy = $c.Y - $ty; $dz = $c.Z - $tz
        $d = $dx * $dx + $dy * $dy + $dz * $dz
        if ($d -lt $bestD) { $bestD = $d; $best = $c }
    }
    return [pscustomobject]@{ Node = $best; Dist = [Math]::Sqrt($bestD); TX = $tx; TY = $ty; TZ = $tz }
}

# ------------------------------------------------------------------------------------
# Main
# ------------------------------------------------------------------------------------
$InFile = (Resolve-Path $InFile).Path
$OutFile = (Resolve-Path $OutFile).Path
if (-not $OutputDir) { $OutputDir = Split-Path $InFile -Parent }
if (-not (Test-Path $OutputDir)) { New-Item -ItemType Directory -Force $OutputDir | Out-Null }
$baseName = [IO.Path]::GetFileNameWithoutExtension($InFile)

Write-Host "Reading node coordinates from $OutFile ..."
$outData = Read-OutNodes $OutFile
$nodes = $outData.Nodes
$info = $outData.Info
Write-Host ("  {0} nodes read ({1} pile, {2} cap, {3} pier, {4} bearing)" -f $nodes.Count,
    @($nodes.Values | Where-Object { $_.Group -eq 'pile' }).Count,
    @($nodes.Values | Where-Object { $_.Group -eq 'cap' }).Count,
    @($nodes.Values | Where-Object { $_.Group -eq 'pier' }).Count,
    @($nodes.Values | Where-Object { $_.Group -like 'bearing*' }).Count)

# Candidate sets per source-node kind
$pileNodes = @($nodes.Values | Where-Object { $_.Group -eq 'pile' })
$pileHeads = @($pileNodes | Where-Object { $_.PileHead })
$capNodes = @($nodes.Values | Where-Object { $_.Group -eq 'cap' }) + $pileHeads
$pierNodes = @($nodes.Values | Where-Object { $_.Group -eq 'pier' })
$bearingNodes = @($nodes.Values | Where-Object { $_.Group -like 'bearing*' })
function Get-Candidates($node) {
    $set = switch ($node.Group) { 'pile' { $pileNodes } 'cap' { $capNodes } 'pier' { $pierNodes } default { if ($node.Group -like 'bearing*') { $bearingNodes } else { @($nodes.Values | Where-Object { $_.Group -eq $node.Group }) } } }
    if ($node.Active) { $act = @($set | Where-Object { $_.Active }); if ($act.Count -gt 0) { return $act } }
    return $set
}

# Mirror planes from the pile cap extents
$capOnly = @($nodes.Values | Where-Object { $_.Group -eq 'cap' })
if ($capOnly.Count -eq 0) { $capOnly = @($nodes.Values) }
$xs = $capOnly | ForEach-Object { $_.X }; $ys = $capOnly | ForEach-Object { $_.Y }
$xmin = ($xs | Measure-Object -Minimum).Minimum; $xmax = ($xs | Measure-Object -Maximum).Maximum
$ymin = ($ys | Measure-Object -Minimum).Minimum; $ymax = ($ys | Measure-Object -Maximum).Maximum
$cx = if ($null -ne $CenterX) { [double]$CenterX } else { ($xmin + $xmax) / 2 }
$cy = if ($null -ne $CenterY) { [double]$CenterY } else { ($ymin + $ymax) / 2 }
Write-Host ("  Pile cap extents X {0} .. {1}, Y {2} .. {3} in;  mirror planes X = {4}, Y = {5}" -f (Fmt $xmin), (Fmt $xmax), (Fmt $ymin), (Fmt $ymax), (Fmt $cx), (Fmt $cy))

# Distinct pile columns/rows (for the p-multiplier list lengths)
$pileXs = @($pileHeads | ForEach-Object { [Math]::Round($_.X, 1) } | Sort-Object -Unique)
$pileYs = @($pileHeads | ForEach-Object { [Math]::Round($_.Y, 1) } | Sort-Object -Unique)
Write-Host ("  {0} piles in {1} X-columns and {2} Y-rows" -f $pileHeads.Count, $pileXs.Count, $pileYs.Count)

# Read the input file
$raw = [IO.File]::ReadAllText($InFile)
$newline = if ($raw.Contains("`r`n")) { "`r`n" } else { "`n" }
$lines = $raw -split "\r?\n"
if ($lines[-1] -eq '') { $lines = $lines[0..($lines.Count - 2)] }
$sec = Read-InSections $lines

# p-multiplier lists in the base file
$grid = Get-PileGridBlock $lines $sec
$basePM = $null
if ($grid) {
    $nSp = ($grid.NPX - 1) + ($grid.NPY - 1)
    $rest = $grid.Tokens.Count - $nSp
    if ($rest -eq ($pileXs.Count + $pileYs.Count)) {
        $basePM = @{
            Spacing = @($grid.Tokens[0..($nSp - 1)])
            X = @($grid.Tokens[$nSp..($nSp + $pileXs.Count - 1)])
            Y = @($grid.Tokens[($nSp + $pileXs.Count)..($grid.Tokens.Count - 1)])
        }
        Write-Host ("  Base p-multipliers: X [{0}]  Y [{1}]" -f ($basePM.X -join ' '), ($basePM.Y -join ' '))
    } else {
        Write-Warning "Could not isolate the p-multiplier lists in the PILE section ($rest trailing values, expected $($pileXs.Count + $pileYs.Count)). They will be left unchanged."
    }
}

# Replacement p-multiplier lists
function Get-PMultFromOtherFile([string]$path) {
    $oLines = [IO.File]::ReadAllText((Resolve-Path $path).Path) -split "\r?\n"
    $oSec = Read-InSections $oLines
    $oGrid = Get-PileGridBlock $oLines $oSec
    if (-not $oGrid) { throw "No pile grid block found in $path" }
    $o0 = ($oGrid.NPX - 1) + ($oGrid.NPY - 1)
    $oRest = $oGrid.Tokens.Count - $o0
    if ($oRest -ne ($pileXs.Count + $pileYs.Count)) { throw "$path has $oRest trailing p-multiplier values; expected $($pileXs.Count + $pileYs.Count) (same pile columns/rows as the base model)." }
    return @{ X = @($oGrid.Tokens[$o0..($o0 + $pileXs.Count - 1)]); Y = @($oGrid.Tokens[($o0 + $pileXs.Count)..($oGrid.Tokens.Count - 1)]) }
}
$pmX = $null; $pmY = $null
if ($PMultXFile) { $pmX = (Get-PMultFromOtherFile $PMultXFile).X }
if ($PMultYFile) { $pmY = (Get-PMultFromOtherFile $PMultYFile).Y }
if ($PMultX) { $pmX = @(($PMultX -split '[,\s]+') | Where-Object { $_ -ne '' }) }
if ($PMultY) { $pmY = @(($PMultY -split '[,\s]+') | Where-Object { $_ -ne '' }) }
if ($pmX -and $pmX.Count -ne $pileXs.Count) { throw "PMultX has $($pmX.Count) values; the model has $($pileXs.Count) pile columns in X." }
if ($pmY -and $pmY.Count -ne $pileYs.Count) { throw "PMultY has $($pmY.Count) values; the model has $($pileYs.Count) pile rows in Y." }
if (($pmX -or $pmY) -and -not $basePM) { throw "P-multiplier replacement requested but the lists could not be isolated in the base file." }
if (($pmX -or $pmY) -and $grid.MultStart -lt 0) { throw "P-multiplier replacement requested but the grid spacing list does not end on a line break in the base file; edit the lists by hand." }

# Bearing count and symmetry check
$nBrg = $info.Bearings
$brgL = @($nodes.Values | Where-Object { $_.Group -eq 'bearingL' } | Sort-Object Id)
$brgR = @($nodes.Values | Where-Object { $_.Group -eq 'bearingR' } | Sort-Object Id)
if (-not $nBrg -and $brgL.Count -gt 0) { $nBrg = $brgL.Count }
$brgWarnings = @()
if ($brgL.Count -gt 0 -and $brgR.Count -eq $brgL.Count) {
    for ($i = 0; $i -lt $brgL.Count; $i++) {
        $j = $brgL.Count - 1 - $i
        $dxm = [Math]::Abs(($brgL[$i].X + $brgL[$j].X) / 2 - $cx)
        if ($dxm -gt 1.0) { $brgWarnings += ("Bearings {0} and {1} are not mirror images about X = {2} (midpoint off by {3} in)" -f ($i + 1), ($j + 1), (Fmt $cx), (Fmt $dxm)) }
    }
    $dym = [Math]::Abs(($brgL[0].Y + $brgR[0].Y) / 2 - $cy)
    if ($dym -gt 1.0) { $brgWarnings += ("Left/right bearing rows are not mirror images about Y = {0} (midpoint off by {1} in)" -f (Fmt $cy), (Fmt $dym)) }
}

# Pier symmetry check (pier nodes should mirror onto pier nodes closely)
$pierCheck = @{}
foreach ($d in @('X', 'Y')) {
    $maxd = 0.0
    foreach ($p in ($pierNodes | Where-Object { $_.Active })) {
        $r = Find-MirrorNode $p $d (@($pierNodes | Where-Object { $_.Active })) $cx $cy
        if ($r.Dist -gt $maxd) { $maxd = $r.Dist }
    }
    $pierCheck[$d] = $maxd
}

$loadRx = [regex]'^(?<pre>\s*)(?<node>\d+)(?<mid>\s+L=\s*(?<case>\d+)\s+F=\s*)(?<f1>\S+)\s+(?<f2>\S+)\s+(?<f3>\S+)\s+(?<f4>\S+)\s+(?<f5>\S+)\s+(?<f6>\S+)(?<rest>.*)$'
$bpRx = [regex]'^(?<pre>\s*)(?<brg>\d+)(?<mid>\s+L=\s*(?<case>\d+)\s+F=\s*)(?<f1>\S+)\s+(?<f2>\S+)\s+(?<f3>\S+)\s+(?<f4>\S+)\s+(?<f5>\S+)\s+(?<f6>\S+)(?<rest1>\s+T=\S+\s+B=\s*)(?<side>[LR])(?<rest2>.*)$'

$typePatterns = @(($MirrorTypes -split '[,\s]+') | Where-Object { $_ -ne '' })
if ($typePatterns.Count -eq 0) { throw "-MirrorTypes is empty; give at least one load type pattern, e.g. 'WS*,WL*'." }
function Test-MirrorType([string]$typ) {
    foreach ($p in $typePatterns) { if ($typ -like $p) { return $true } }
    return $false
}
function Get-LineType([string]$text) { if ($text -match 'T=\s*(\S+)') { return $Matches[1] } else { return '' } }
Write-Host ("  Load types mirrored: {0}  (all other types copied unchanged)" -f ($typePatterns -join ', '))

$dirs = if ($Mirror -eq 'All') { @('X', 'Y', 'XY') } else { @($Mirror) }
$written = @()

foreach ($dir in $dirs) {
    Write-Host ""
    Write-Host "=== Mirror $dir ==="
    $flip = Get-FlipMask $dir
    $map = @{}          # source node id -> mapping record
    $out = New-Object System.Collections.Generic.List[string]
    $missing = @{}
    $nLoad = 0; $nBp = 0
    $keptTypes = @{}    # type -> number of lines copied unchanged
    $mirTypes = @{}     # type -> number of lines mirrored
    $sums = @{}         # case -> @{ Type; oF; oM; nF; nM } (resultants about the mirror center, forces only)

    for ($i = 0; $i -lt $lines.Count; $i++) {
        $l = $lines[$i]
        $s = $sec[$i]

        if ($s -eq 'LOAD') {
            $m = $loadRx.Match($l)
            if ($m.Success) {
                $lt = Get-LineType $m.Groups['rest'].Value
                if (-not (Test-MirrorType $lt)) { $keptTypes[$lt] = 1 + [int]$keptTypes[$lt]; $out.Add($l); continue }
                $mirTypes[$lt] = 1 + [int]$mirTypes[$lt]
                $nLoad++
                $id = [int]$m.Groups['node'].Value
                $vals = @($m.Groups['f1'].Value, $m.Groups['f2'].Value, $m.Groups['f3'].Value, $m.Groups['f4'].Value, $m.Groups['f5'].Value, $m.Groups['f6'].Value)
                $newVals = @(); for ($k = 0; $k -lt 6; $k++) { $newVals += if ($flip[$k]) { NegText $vals[$k] } else { $vals[$k] } }
                $newId = $id
                if ($nodes.ContainsKey($id)) {
                    if (-not $map.ContainsKey($id)) {
                        $src = $nodes[$id]
                        $r = Find-MirrorNode $src $dir (Get-Candidates $src) $cx $cy
                        $map[$id] = [pscustomobject]@{
                            Node = $id; Kind = $src.Group; Pile = $src.Pile; X = $src.X; Y = $src.Y; Z = $src.Z
                            MirrorX = $r.TX; MirrorY = $r.TY; MirrorZ = $r.TZ
                            NewNode = $r.Node.Id; NewPile = $r.Node.Pile; NewX = $r.Node.X; NewY = $r.Node.Y; NewZ = $r.Node.Z
                            Offset = $r.Dist; Flag = $(if ($r.Dist -gt $Tolerance) { 'CHECK' } elseif ($r.Dist -gt 1.0) { 'approx' } else { '' })
                            Loads = 0
                        }
                    }
                    $map[$id].Loads++
                    $newId = $map[$id].NewNode
                    # resultant bookkeeping (forces about the mirror center)
                    $case = $m.Groups['case'].Value
                    $typ = if ($m.Groups['rest'].Value -match 'T=(\S+)') { $Matches[1] } else { '' }
                    if (-not $sums.ContainsKey($case)) { $sums[$case] = @{ Type = $typ; oF = @(0.0, 0.0, 0.0); oM = @(0.0, 0.0, 0.0); nF = @(0.0, 0.0, 0.0); nM = @(0.0, 0.0, 0.0) } }
                    $F = @((ToDouble $vals[0]), (ToDouble $vals[1]), (ToDouble $vals[2]))
                    $nF = @((ToDouble $newVals[0]), (ToDouble $newVals[1]), (ToDouble $newVals[2]))
                    $src = $nodes[$id]; $dst = $nodes[$newId]
                    $ro = @(($src.X - $cx), ($src.Y - $cy), $src.Z); $rn = @(($dst.X - $cx), ($dst.Y - $cy), $dst.Z)
                    for ($k = 0; $k -lt 3; $k++) { $sums[$case].oF[$k] += $F[$k]; $sums[$case].nF[$k] += $nF[$k] }
                    $sums[$case].oM[0] += $ro[1] * $F[2] - $ro[2] * $F[1]; $sums[$case].oM[1] += $ro[2] * $F[0] - $ro[0] * $F[2]; $sums[$case].oM[2] += $ro[0] * $F[1] - $ro[1] * $F[0]
                    $sums[$case].nM[0] += $rn[1] * $nF[2] - $rn[2] * $nF[1]; $sums[$case].nM[1] += $rn[2] * $nF[0] - $rn[0] * $nF[2]; $sums[$case].nM[2] += $rn[0] * $nF[1] - $rn[1] * $nF[0]
                } else {
                    $missing[$id] = $true
                }
                $out.Add($m.Groups['pre'].Value + $newId + $m.Groups['mid'].Value + ($newVals -join ' ') + $m.Groups['rest'].Value)
                continue
            }
        }
        elseif ($s -eq 'LOADBP') {
            $m = $bpRx.Match($l)
            if ($m.Success) {
                $lt = Get-LineType $m.Groups['rest1'].Value
                if (-not (Test-MirrorType $lt)) { $keptTypes[$lt] = 1 + [int]$keptTypes[$lt]; $out.Add($l); continue }
                $mirTypes[$lt] = 1 + [int]$mirTypes[$lt]
                $nBp++
                $brg = [int]$m.Groups['brg'].Value
                $side = $m.Groups['side'].Value
                $vals = @($m.Groups['f1'].Value, $m.Groups['f2'].Value, $m.Groups['f3'].Value, $m.Groups['f4'].Value, $m.Groups['f5'].Value, $m.Groups['f6'].Value)
                $newVals = @(); for ($k = 0; $k -lt 6; $k++) { $newVals += if ($flip[$k]) { NegText $vals[$k] } else { $vals[$k] } }
                if ($dir -like '*X*') { if (-not $nBrg) { throw "Bearing count unknown; cannot mirror LOADBP in X." }; $brg = $nBrg + 1 - $brg }
                if ($dir -like '*Y*') { $side = if ($side -eq 'L') { 'R' } else { 'L' } }
                $out.Add($m.Groups['pre'].Value + $brg + $m.Groups['mid'].Value + ($newVals -join ' ') + $m.Groups['rest1'].Value + $side + $m.Groups['rest2'].Value)
                continue
            }
        }
        elseif ($s -eq 'PROBLEM' -and $i + 1 -lt $lines.Count -and $sec[$i + 1] -eq 'PROBLEM#END') {
            $suffix = if ($TitleSuffix) { $TitleSuffix } elseif ($typePatterns -contains '*') { " - Mirror $dir all loads" } else { " - Mirror $dir " + (($typePatterns | ForEach-Object { $_ -replace '\*', '' }) -join ' ') }
            $out.Add($l.TrimEnd() + $suffix)
            continue
        }
        elseif ($grid -and $basePM -and (($pmX -and $dir -like '*X*') -or ($pmY -and $dir -like '*Y*')) -and $i -ge $grid.MultStart -and $i -le $grid.LastLine) {
            # p-multiplier lines: rewrite once (at the first multiplier line), skip the rest; spacing lines stay untouched
            if ($i -eq $grid.MultStart) {
                $useX = if ($pmX -and $dir -like '*X*') { $pmX } else { $basePM.X }
                $useY = if ($pmY -and $dir -like '*Y*') { $pmY } else { $basePM.Y }
                foreach ($t in (Format-NumberLines $useX 10 $false)) { $out.Add($t) }
                foreach ($t in (Format-NumberLines $useY 10 $false)) { $out.Add($t) }
            }
            continue
        }
        $out.Add($l)
    }

    # ---- write files
    $outPath = Join-Path $OutputDir ("{0}_Mirror{1}.in" -f $baseName, $dir)
    [IO.File]::WriteAllText($outPath, (($out -join $newline) + $newline), (New-Object Text.UTF8Encoding $false))
    $written += $outPath

    $csvPath = Join-Path $OutputDir ("{0}_Mirror{1}_nodes.csv" -f $baseName, $dir)
    $map.Values | Sort-Object Node | Select-Object Node, Kind, Pile, Loads,
        @{n = 'X'; e = { Fmt $_.X }}, @{n = 'Y'; e = { Fmt $_.Y }}, @{n = 'Z'; e = { Fmt $_.Z }},
        @{n = 'MirrorX'; e = { Fmt $_.MirrorX }}, @{n = 'MirrorY'; e = { Fmt $_.MirrorY }},
        NewNode, NewPile, @{n = 'NewX'; e = { Fmt $_.NewX }}, @{n = 'NewY'; e = { Fmt $_.NewY }}, @{n = 'NewZ'; e = { Fmt $_.NewZ }},
        @{n = 'Offset_in'; e = { Fmt $_.Offset 2 }}, Flag |
        Export-Csv -Path $csvPath -NoTypeInformation -Encoding UTF8

    # ---- report
    $rep = New-Object System.Collections.Generic.List[string]
    $rep.Add("FB-MultiPier load mirroring report  -  Mirror $dir")
    $rep.Add("Generated : " + (Get-Date).ToString('yyyy-MM-dd HH:mm'))
    $rep.Add("Base .in  : $InFile")
    $rep.Add("Base .out : $OutFile")
    $rep.Add("Output    : $outPath")
    $rep.Add("")
    $rep.Add(("Mirror planes: X = {0} in, Y = {1} in (pile cap extents X {2}..{3}, Y {4}..{5})" -f (Fmt $cx), (Fmt $cy), (Fmt $xmin), (Fmt $xmax), (Fmt $ymin), (Fmt $ymax)))
    $rep.Add(("Sign changes applied: " + (@('Fx', 'Fy', 'Fz', 'Mx', 'My', 'Mz') | Where-Object { $flip[[array]::IndexOf(@('Fx', 'Fy', 'Fz', 'Mx', 'My', 'Mz'), $_)] }) -join ', '))
    $rep.Add(("Pier symmetry: largest mirror-node offset among pier nodes = X {0} in, Y {1} in" -f (Fmt $pierCheck['X'] 2), (Fmt $pierCheck['Y'] 2)))
    foreach ($w in $brgWarnings) { $rep.Add("WARNING: $w") }
    $rep.Add("")
    $sortT = { [regex]::Replace($_.Name, '\d+', { param($x) $x.Value.PadLeft(4, '0') }) }
    $rep.Add("Load types mirrored (patterns " + ($typePatterns -join ', ') + "):")
    $rep.Add("  " + $(if ($mirTypes.Count) { (($mirTypes.GetEnumerator() | Sort-Object $sortT | ForEach-Object { "{0} x{1}" -f $_.Name, $_.Value }) -join ', ') } else { '(none found - check -MirrorTypes)' }))
    $rep.Add("Load types copied unchanged:")
    $rep.Add("  " + $(if ($keptTypes.Count) { (($keptTypes.GetEnumerator() | Sort-Object $sortT | ForEach-Object { "{0} x{1}" -f $(if ($_.Name) { $_.Name } else { '(no type)' }), $_.Value }) -join ', ') } else { '(none)' }))
    $rep.Add("")
    $rep.Add(("LOAD lines rewritten   : {0}  ({1} distinct nodes)" -f $nLoad, $map.Count))
    $rep.Add(("LOADBP lines rewritten : {0}  (bearings = {1}; index mirrored = {2}; L/R swapped = {3})" -f $nBp, $nBrg, ($dir -like '*X*'), ($dir -like '*Y*')))
    if ($missing.Count -gt 0) { $rep.Add("WARNING: nodes not found in the .out file (left unchanged): " + (($missing.Keys | Sort-Object) -join ', ')) }
    $byKind = $map.Values | Group-Object Kind
    foreach ($g in $byKind) {
        $mx = ($g.Group | Measure-Object Offset -Maximum).Maximum
        $rep.Add(("  {0,-8} {1,4} nodes, largest offset between ideal mirror point and chosen node = {2} in" -f $g.Name, $g.Count, (Fmt $mx 2)))
    }
    $checks = @($map.Values | Where-Object { $_.Flag -eq 'CHECK' })
    if ($checks.Count -gt 0) {
        $rep.Add("")
        $rep.Add("CHECK: the following nodes were matched farther than the tolerance ($Tolerance in):")
        foreach ($c in ($checks | Sort-Object Node)) { $rep.Add(("  node {0} ({1}) at ({2}, {3}, {4}) -> node {5} at ({6}, {7}, {8}), offset {9} in" -f $c.Node, $c.Kind, (Fmt $c.X 1), (Fmt $c.Y 1), (Fmt $c.Z 1), $c.NewNode, (Fmt $c.NewX 1), (Fmt $c.NewY 1), (Fmt $c.NewZ 1), (Fmt $c.Offset 2))) }
    }
    $dups = @($map.Values | Group-Object NewNode | Where-Object { $_.Count -gt 1 })
    if ($dups.Count -gt 0) {
        $rep.Add("")
        $rep.Add("WARNING: more than one loaded node was mapped onto the same target node (loads would stack):")
        foreach ($g in $dups) { $rep.Add(("  target node {0} <- source nodes {1}" -f $g.Name, (($g.Group | ForEach-Object { $_.Node }) -join ', '))) }
    }
    $rep.Add("")
    if ($basePM) {
        $useX = if ($pmX -and $dir -like '*X*') { $pmX } else { $basePM.X }
        $useY = if ($pmY -and $dir -like '*Y*') { $pmY } else { $basePM.Y }
        $rep.Add("P-multipliers written (trail -> lead order, as FB-MultiPier applies them by displacement direction):")
        $rep.Add("  X: " + ($useX -join ' ') + $(if ($pmX -and $dir -like '*X*') { '   [replaced]' } else { '   [unchanged from base - review if calibrated per direction]' }))
        $rep.Add("  Y: " + ($useY -join ' ') + $(if ($pmY -and $dir -like '*Y*') { '   [replaced]' } else { '   [unchanged from base - review if calibrated per direction]' }))
    } else { $rep.Add("P-multipliers: not modified.") }
    $rep.Add("")
    $rep.Add("Resultant check per load case (nodal loads only; forces in file units, moments of forces about the mirror")
    $rep.Add("planes in force x inches). 'expected' is the mirrored original; differences come from node offsets.")
    $rep.Add(("  {0,-5} {1,-6} {2,10} {3,10} {4,10} {5,12} {6,12} {7,12} {8,12} {9,12} {10,12}" -f 'case', 'type', 'sumFx', 'sumFy', 'sumFz', 'Mx(new)', 'Mx(exp)', 'My(new)', 'My(exp)', 'Mz(new)', 'Mz(exp)'))
    $sx = if ($flip[0]) { -1 } else { 1 }; $sy = if ($flip[1]) { -1 } else { 1 }; $sz = 1
    # a mirror of positions and forces maps the moment of forces as: Mx -> sxs*..; derive from vector mirror: M' = det(S) * S * M
    $S = @($sx, $sy, $sz); $det = $sx * $sy * $sz
    foreach ($case in ($sums.Keys | Sort-Object { [int]$_ })) {
        $r = $sums[$case]
        $exp = @(($det * $S[0] * $r.oM[0]), ($det * $S[1] * $r.oM[1]), ($det * $S[2] * $r.oM[2]))
        $rep.Add(("  {0,-5} {1,-6} {2,10} {3,10} {4,10} {5,12} {6,12} {7,12} {8,12} {9,12} {10,12}" -f $case, $r.Type, (Fmt $r.nF[0] 2), (Fmt $r.nF[1] 2), (Fmt $r.nF[2] 2), (Fmt $r.nM[0] 1), (Fmt $exp[0] 1), (Fmt $r.nM[1] 1), (Fmt $exp[1] 1), (Fmt $r.nM[2] 1), (Fmt $exp[2] 1)))
    }
    $rep.Add("")
    $rep.Add("Notes:")
    $rep.Add("  - Wind cases (WS*) are mirrored as nodal loads. Do not regenerate wind loads from the WIND page in the GUI")
    $rep.Add("    for this file, or the mirrored loads will be overwritten.")
    $rep.Add("  - The foundation (pile grid, missing piles, cap, soil) is identical to the base model, so results are in")
    $rep.Add("    as-built pile numbering.")
    $repPath = Join-Path $OutputDir ("{0}_Mirror{1}_report.txt" -f $baseName, $dir)
    [IO.File]::WriteAllLines($repPath, $rep)
    $written += $csvPath, $repPath

    Write-Host ("  Mirrored: {0} load lines, {1} bearing-load lines ({2} distinct nodes mapped)" -f $nLoad, $nBp, $map.Count)
    Write-Host ("  Unchanged: {0} lines of types {1}" -f (($keptTypes.Values | Measure-Object -Sum).Sum), ((($keptTypes.Keys | ForEach-Object { $_ -replace '\d+$', '' }) | Sort-Object -Unique) -join ', '))
    if ($mirTypes.Count -eq 0) { Write-Warning "No load lines matched -MirrorTypes '$MirrorTypes'; the generated file equals the base model." }
    foreach ($g in $byKind) { Write-Host ("    {0,-8} largest offset {1} in" -f $g.Name, (Fmt (($g.Group | Measure-Object Offset -Maximum).Maximum) 2)) }
    if ($checks.Count -gt 0) { Write-Warning ("{0} node(s) matched beyond the tolerance; see {1}" -f $checks.Count, $repPath) }
    if ($dups.Count -gt 0) { Write-Warning ("{0} target node(s) receive loads from more than one source node; see {1}" -f $dups.Count, $repPath) }
    foreach ($w in $brgWarnings) { Write-Warning $w }
    Write-Host "  Wrote $outPath"
}

Write-Host ""
Write-Host "Done. Files written:"
$written | ForEach-Object { Write-Host "  $_" }
