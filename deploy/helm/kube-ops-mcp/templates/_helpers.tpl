{{- define "kube-ops-mcp.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "kube-ops-mcp.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else if contains (include "kube-ops-mcp.name" .) .Release.Name }}
{{- .Release.Name | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s-%s" .Release.Name (include "kube-ops-mcp.name" .) | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}

{{- define "kube-ops-mcp.selectorLabels" -}}
app.kubernetes.io/name: {{ include "kube-ops-mcp.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{- define "kube-ops-mcp.labels" -}}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" }}
{{ include "kube-ops-mcp.selectorLabels" . }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{/* Host headers accepted by the server (DNS rebinding protection). */}}
{{- define "kube-ops-mcp.allowedHosts" -}}
{{- $name := include "kube-ops-mcp.fullname" . -}}
{{- $ns := .Release.Namespace -}}
{{- $hosts := list "localhost:*" "127.0.0.1:*" (printf "%s:*" $name) (printf "%s.%s:*" $name $ns) (printf "%s.%s.svc:*" $name $ns) (printf "%s.%s.svc.cluster.local:*" $name $ns) -}}
{{- concat $hosts .Values.extraAllowedHosts | join "," -}}
{{- end }}
