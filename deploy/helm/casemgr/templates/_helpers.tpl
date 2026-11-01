{{- define "casemgr.labels" -}}
app.kubernetes.io/name: casemgr
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{- define "casemgr.selector" -}}
app.kubernetes.io/name: casemgr
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{/* Pod Security "restricted" settings shared by every pod. */}}
{{- define "casemgr.podSecurity" -}}
automountServiceAccountToken: false
securityContext:
  runAsNonRoot: true
  seccompProfile:
    type: RuntimeDefault
{{- end }}

{{- define "casemgr.containerSecurity" -}}
securityContext:
  allowPrivilegeEscalation: false
  readOnlyRootFilesystem: true
  capabilities:
    drop: [ALL]
{{- end }}

{{/* Environment for the backend image. Takes (dict "ctx" $ "role" "runtime"|"owner"). */}}
{{- define "casemgr.backendEnv" -}}
{{- $ := .ctx -}}
{{- $db := $.Values.database -}}
envFrom:
  - secretRef:
      name: {{ $.Values.secretName }}
env:
  - name: DB_USER
    valueFrom:
      secretKeyRef:
        name: {{ eq .role "owner" | ternary $db.ownerSecret $db.runtimeSecret }}
        key: username
  - name: DB_PASSWORD
    valueFrom:
      secretKeyRef:
        name: {{ eq .role "owner" | ternary $db.ownerSecret $db.runtimeSecret }}
        key: password
  # TLS to Postgres, verified against the CA that CloudNativePG issues for the cluster.
  - name: {{ eq .role "owner" | ternary "CASEMGR_MIGRATION_DATABASE_URL" "CASEMGR_DATABASE_URL" }}
    value: postgresql+asyncpg://$(DB_USER):$(DB_PASSWORD)@{{ $db.cluster }}-rw:5432/{{ $db.name }}?ssl=verify-full
  - name: PGSSLROOTCERT
    value: /etc/casemgr/db-ca/ca.crt
  - name: CASEMGR_PUBLIC_URL
    value: {{ $.Values.publicUrl | quote }}
  {{- range $key, $value := $.Values.config }}
  - name: CASEMGR_{{ $key }}
    value: {{ $value | quote }}
  {{- end }}
volumeMounts:
  - name: tmp
    mountPath: /tmp
  - name: db-ca
    mountPath: /etc/casemgr/db-ca
    readOnly: true
{{- end }}

{{- define "casemgr.backendVolumes" -}}
volumes:
  - name: tmp
    emptyDir: {}
  - name: db-ca
    secret:
      secretName: {{ .Values.database.cluster }}-ca
      items:
        - key: ca.crt
          path: ca.crt
{{- end }}

{{/* A pod that runs the backend image once: a migration, a hook, or a scheduled job. */}}
{{- define "casemgr.jobPod" -}}
{{- $ := .ctx -}}
metadata:
  labels:
    {{- include "casemgr.selector" $ | nindent 4 }}
    app.kubernetes.io/component: job
    casemgr.io/database-client: "true"
spec:
  restartPolicy: Never
  {{- include "casemgr.podSecurity" $ | nindent 2 }}
  containers:
    - name: job
      image: {{ $.Values.images.backend }}
      imagePullPolicy: {{ $.Values.images.pullPolicy }}
      command: {{ toJson .command }}
      {{- include "casemgr.containerSecurity" $ | nindent 6 }}
      resources:
        {{- toYaml $.Values.jobResources | nindent 8 }}
      {{- include "casemgr.backendEnv" (dict "ctx" $ "role" (.role | default "runtime")) | nindent 6 }}
  {{- include "casemgr.backendVolumes" $ | nindent 2 }}
{{- end }}
