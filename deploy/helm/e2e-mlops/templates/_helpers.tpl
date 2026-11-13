{{- define "e2e-mlops.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "e2e-mlops.fullname" -}}
{{- if .Values.fullnameOverride -}}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" -}}
{{- else if contains (include "e2e-mlops.name" .) .Release.Name -}}
{{- .Release.Name | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- printf "%s-%s" .Release.Name (include "e2e-mlops.name" .) | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- end -}}

{{- define "e2e-mlops.labels" -}}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" }}
app.kubernetes.io/name: {{ include "e2e-mlops.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/part-of: e2e-mlops
{{- end -}}

{{/* Selector labels for one component: include "e2e-mlops.selector" (list . "inference") */}}
{{- define "e2e-mlops.selector" -}}
{{- $root := index . 0 -}}
app.kubernetes.io/name: {{ include "e2e-mlops.name" $root }}
app.kubernetes.io/instance: {{ $root.Release.Name }}
app.kubernetes.io/component: {{ index . 1 }}
{{- end -}}

{{- define "e2e-mlops.serviceAccountName" -}}
{{- if .Values.serviceAccount.create -}}
{{- default (include "e2e-mlops.fullname" .) .Values.serviceAccount.name -}}
{{- else -}}
{{- default "default" .Values.serviceAccount.name -}}
{{- end -}}
{{- end -}}

{{/* Image reference: include "e2e-mlops.image" (list . "inference") */}}
{{- define "e2e-mlops.image" -}}
{{- $root := index . 0 -}}
{{- printf "%s/%s:%s" (trimSuffix "/" $root.Values.image.repository) (index . 1) (toString $root.Values.image.tag) -}}
{{- end -}}

{{/* Pod-level settings shared by every workload. */}}
{{- define "e2e-mlops.podCommon" -}}
serviceAccountName: {{ include "e2e-mlops.serviceAccountName" . }}
automountServiceAccountToken: false
securityContext:
  {{- toYaml .Values.podSecurityContext | nindent 2 }}
{{- with .Values.imagePullSecrets }}
imagePullSecrets:
  {{- toYaml . | nindent 2 }}
{{- end }}
{{- end -}}

{{/* In-cluster URL of the MLflow tracking server. */}}
{{- define "e2e-mlops.trackingUrl" -}}
{{- printf "http://%s-mlflow:%v" (include "e2e-mlops.fullname" .) .Values.tracking.port -}}
{{- end -}}

{{- define "e2e-mlops.postgresHost" -}}
{{- if .Values.postgres.enabled -}}
{{- printf "%s-postgres" (include "e2e-mlops.fullname" .) -}}
{{- else -}}
{{- required "postgres.externalHost is required when postgres.enabled=false" .Values.postgres.externalHost -}}
{{- end -}}
{{- end -}}

{{- define "e2e-mlops.s3Endpoint" -}}
{{- if eq .Values.objectStore.mode "internal" -}}
{{- printf "http://%s-seaweedfs:%v" (include "e2e-mlops.fullname" .) .Values.objectStore.internal.s3Port -}}
{{- else if eq .Values.objectStore.mode "external" -}}
{{- required "objectStore.external.endpoint is required in external mode" .Values.objectStore.external.endpoint -}}
{{- else -}}
{{- fail "objectStore.mode must be internal or external" -}}
{{- end -}}
{{- end -}}

{{/* env entries: one variable per key of a Secret, e.g. (list "NAME" "secret" "key") */}}
{{- define "e2e-mlops.secretEnv" -}}
- name: {{ index . 0 }}
  valueFrom:
    secretKeyRef:
      name: {{ index . 1 }}
      key: {{ index . 2 }}
{{- end -}}

{{/* MLflow client settings for a service user: (list $ "mlflowTrainer") */}}
{{- define "e2e-mlops.mlflowClientEnv" -}}
{{- $root := index . 0 -}}
{{- $secret := index $root.Values.secrets (index . 1) -}}
- name: MLFLOW_TRACKING_URI
  value: {{ include "e2e-mlops.trackingUrl" $root | quote }}
{{ include "e2e-mlops.secretEnv" (list "MLFLOW_TRACKING_USERNAME" $secret "username") }}
{{ include "e2e-mlops.secretEnv" (list "MLFLOW_TRACKING_PASSWORD" $secret "password") }}
- name: MLFLOW_HTTP_REQUEST_MAX_RETRIES
  value: "3"
{{- end -}}

{{/* Helm hook annotations: (list "post-install,post-upgrade" weight) */}}
{{- define "e2e-mlops.hook" -}}
helm.sh/hook: {{ index . 0 }}
helm.sh/hook-weight: {{ index . 1 | quote }}
helm.sh/hook-delete-policy: before-hook-creation,hook-succeeded
{{- end -}}

{{/* Inference mode: "deployment" or "kserve" (anything else fails the render). */}}
{{- define "e2e-mlops.inferenceMode" -}}
{{- $mode := .Values.inference.mode | default "deployment" -}}
{{- if not (has $mode (list "deployment" "kserve")) -}}
{{- fail (printf "inference.mode must be deployment or kserve, not %q" $mode) -}}
{{- end -}}
{{- $mode -}}
{{- end -}}

{{/* Model the predictor loads: the pinned registry version, else the alias URI. */}}
{{- define "e2e-mlops.modelUri" -}}
{{- $version := .Values.inference.championVersion -}}
{{- if and $version (ne (toString $version) "") -}}
{{- if not (regexMatch "^[1-9][0-9]*$" (toString $version)) -}}
{{- fail (printf "inference.championVersion must be a registry version number, not %q" (toString $version)) -}}
{{- end -}}
{{- printf "models:/%s/%s" .Values.tracking.modelName (toString $version) -}}
{{- else -}}
{{- .Values.inference.modelUri -}}
{{- end -}}
{{- end -}}

{{/* "true" when the feature logger is rendered: enabled, and inference runs on KServe
     (only InferenceService pods have the logger sidecar). */}}
{{- define "e2e-mlops.featureLogActive" -}}
{{- if and .Values.featureLog.enabled .Values.inference.enabled (eq (include "e2e-mlops.inferenceMode" .) "kserve") -}}
{{- if not .Values.tracking.enabled -}}
{{- fail "featureLog.enabled requires tracking.enabled (the feature log lives in its PostgreSQL)" -}}
{{- end -}}
true
{{- end -}}
{{- end -}}

{{/* In-cluster URL KServe's logger sidecar sends events to. */}}
{{- define "e2e-mlops.featureLogUrl" -}}
{{- printf "http://%s-feature-logger.%s.svc.cluster.local/" (include "e2e-mlops.fullname" .) .Release.Namespace -}}
{{- end -}}

{{/* Environment of the inference container, shared by both modes. */}}
{{- define "e2e-mlops.inferenceEnv" -}}
- name: MODEL_URI
  value: {{ include "e2e-mlops.modelUri" . | quote }}
{{- if .Values.tracking.enabled }}
{{ include "e2e-mlops.mlflowClientEnv" (list . "mlflowServing") }}
{{- end }}
{{- end -}}
