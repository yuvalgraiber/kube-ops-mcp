# Recorded with https://github.com/charmbracelet/vhs (`make demo-gif`).
# Needs the demo cluster (make demo-up), Prometheus on :9090 (make port-forward) and an
# approved kube-ops server in Claude Code.
Output docs/demo.gif

Set Shell "bash"
Set FontSize 15
Set Width 1300
Set Height 820
Set Padding 18
Set Theme "Catppuccin Mocha"
Set TypingSpeed 45ms

Hide
Type "clear" Enter
Show

Type "kubectl -n demo get pods" Sleep 300ms Enter
Sleep 2.5s
Type "claude" Sleep 300ms Enter
Sleep 6s
Type "/triage payment-service" Sleep 600ms Enter
Sleep 32s
