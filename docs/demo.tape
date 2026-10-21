# Recorded with https://github.com/charmbracelet/vhs (`make demo-gif`).
# Needs the demo cluster (make demo-up), Prometheus on :9090 (make port-forward) and an
# approved kube-ops server in Claude Code. The wait for the model is cut out.
Output docs/demo.gif

Set Shell "bash"
Set FontSize 15
Set Width 1300
Set Height 820
Set Padding 18
Set Theme "Catppuccin Mocha"
Set TypingSpeed 25ms

Hide
Type "clear" Enter
Show

Type "kubectl -n demo get pods" Enter
Sleep 2.5s
Hide
Type "claude" Enter
Wait+Screen@30s /❯/
Sleep 1s
Show
Type "/triage payment-service" Sleep 300ms Enter
Wait+Screen@60s /Calling kube-ops/
Sleep 2s
Hide
Wait+Screen@180s /done \d/
Show
Sleep 7s
