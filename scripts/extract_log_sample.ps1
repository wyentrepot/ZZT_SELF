param(
    [Parameter(Mandatory = $true)]
    [string]$Source,

    [Parameter(Mandatory = $true)]
    [string]$Destination,

    [ValidateRange(1, 100000)]
    [int]$MaxLines = 500,

    [ValidateRange(1024, 104857600)]
    [int]$MaxBytes = 1048576
)

$sourcePath = [System.IO.Path]::GetFullPath($Source)
$destinationPath = [System.IO.Path]::GetFullPath($Destination)

if (-not [System.IO.File]::Exists($sourcePath)) {
    throw "源日志不存在：$sourcePath"
}

$destinationDirectory = [System.IO.Path]::GetDirectoryName($destinationPath)
[System.IO.Directory]::CreateDirectory($destinationDirectory) | Out-Null

$reader = [System.IO.File]::OpenText($sourcePath)
$writer = New-Object System.IO.StreamWriter($destinationPath, $false, [System.Text.UTF8Encoding]::new($false))

try {
    $lineCount = 0
    $writtenBytes = 0

    while ($lineCount -lt $MaxLines -and -not $reader.EndOfStream) {
        $line = $reader.ReadLine()
        $lineBytes = [System.Text.Encoding]::UTF8.GetByteCount($line) + 1
        if ($writtenBytes + $lineBytes -gt $MaxBytes) {
            break
        }

        $writer.WriteLine($line)
        $lineCount++
        $writtenBytes += $lineBytes
    }
}
finally {
    $writer.Dispose()
    $reader.Dispose()
}

[PSCustomObject]@{
    Source = $sourcePath
    Destination = $destinationPath
    Lines = $lineCount
    Bytes = (Get-Item -LiteralPath $destinationPath).Length
}
