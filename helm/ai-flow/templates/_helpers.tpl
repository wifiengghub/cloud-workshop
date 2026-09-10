{{- define "ai-flow.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "ai-flow.fullname" -}}
{{- default .Release.Name .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "ai-flow.labels" -}}
app.kubernetes.io/name: {{ include "ai-flow.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}
