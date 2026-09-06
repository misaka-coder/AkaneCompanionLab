param([Parameter(Mandatory=$true)][string]$OutputPath)
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Speech
$fixtureVoice = New-Object System.Speech.Synthesis.SpeechSynthesizer
try {
    $fixtureVoice.SelectVoice('Microsoft Zira Desktop')
    $fixtureVoice.Rate = -1
    $fixtureVoice.SetOutputToWaveFile($OutputPath)
    $fixtureVoice.Speak('Hello world. This is a test of speech recognition. The quick brown fox jumps over the lazy dog.')
} finally {
    $fixtureVoice.Dispose()
}
