# Recorded with https://github.com/charmbracelet/vhs: `vhs docs/demo.tape`, which writes
# docs/demo-raw.mp4. docs/demo.gif is that recording with the two waits for the model
# played at 3x speed and each answer held for about 4 seconds (ffmpeg trim + setpts).
#
# Needs the demo cluster (make demo-up), Prometheus on :9090 (make port-forward) and an
# approved kube-ops server in Claude Code. File and shell tools are turned off so every
# answer comes from the cluster, not from reading demo/broken-apps.yaml.
Output docs/demo-raw.mp4

Set Shell "bash"
Set FontSize 15
Set Width 1300
Set Height 820
Set Padding 18
Set Theme "Catppuccin Mocha"
Set TypingSpeed 35ms
Set Framerate 30

Hide
Type "export DISABLE_AUTOUPDATER=1; clear" Enter
Show

Type "kubectl -n demo get pods" Enter
Sleep 3s
Hide
Type "claude --disallowedTools Bash,Read,Grep,Glob,Edit,Write,WebFetch,WebSearch" Enter
Wait+Screen@30s /❯/
Sleep 1s
Show

Type "/triage payment-service" Sleep 400ms Enter
Wait+Screen@180s /done \d/
Sleep 8s

Hide
Type "/clear" Enter
Sleep 2s
Show
Type "/triage recommendation-service" Sleep 400ms Enter
Wait+Screen@180s /done \d/
Sleep 10s
