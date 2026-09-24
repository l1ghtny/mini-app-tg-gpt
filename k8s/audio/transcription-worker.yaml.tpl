apiVersion: apps/v1
kind: Deployment
metadata:
  name: __WORKER_NAME__
  namespace: gpt
  labels:
    app.kubernetes.io/part-of: lightny-audio-transcription
spec:
  replicas: 1
  revisionHistoryLimit: 3
  strategy:
    type: RollingUpdate
    rollingUpdate:
      maxSurge: 1
      maxUnavailable: 0
  selector:
    matchLabels:
      app: __WORKER_NAME__
  template:
    metadata:
      labels:
        app: __WORKER_NAME__
    spec:
      automountServiceAccountToken: false
      terminationGracePeriodSeconds: 2460
      containers:
      - name: worker
        image: __BACKEND_IMAGE__
        imagePullPolicy: Always
        workingDir: /app
        command:
        - python
        - -m
        - jobs.audio_transcription_worker
        env:
        - name: DEPLOYMENT_CHANNEL
          value: __CHANNEL__
        - name: VOICE_TRANSCRIPTION_ENABLED
          value: 'true'
        - name: VOICE_TRANSCRIPTION_MODEL
          value: gpt-transcribe
        - name: VOICE_TRANSCRIPTION_UPLOAD_MAX_BYTES
          value: '20971520'
        - name: VOICE_TRANSCRIPTION_UPLOAD_MAX_DURATION_SECONDS
          value: '1800'
        - name: VOICE_TRANSCRIPTION_COST_PER_MINUTE_USD
          value: '0.0045'
        - name: AUDIO_TRANSCRIPTION_R2_BUCKET
          valueFrom:
            secretKeyRef:
              name: tg-mini-beta-work-runs-r2
              key: R2_PRIVATE_DOCUMENTS_BUCKET
        - name: AUDIO_TRANSCRIPTION_R2_ACCESS_KEY_ID
          valueFrom:
            secretKeyRef:
              name: tg-mini-beta-work-runs-r2
              key: R2_PRIVATE_DOCUMENTS_ACCESS_KEY_ID
        - name: AUDIO_TRANSCRIPTION_R2_SECRET_ACCESS_KEY
          valueFrom:
            secretKeyRef:
              name: tg-mini-beta-work-runs-r2
              key: R2_PRIVATE_DOCUMENTS_SECRET_ACCESS_KEY
        - name: AUDIO_TRANSCRIPTION_R2_ENDPOINT
          valueFrom:
            secretKeyRef:
              name: __BACKEND_ENV_SECRET__
              key: R2_ENDPOINT
        - name: HTTP_PROXY
          value: socks5://warp-proxy:1080
        - name: HTTPS_PROXY
          value: socks5://warp-proxy:1080
        envFrom:
        - secretRef:
            name: __BACKEND_ENV_SECRET__
        resources:
          requests:
            cpu: 200m
            memory: 256Mi
          limits:
            cpu: '1'
            memory: 1Gi
        readinessProbe:
          exec:
            command:
            - python
            - -c
            - import os,time; assert time.time()-os.path.getmtime('/tmp/audio-worker-heartbeat')
              < 60
          initialDelaySeconds: 5
          periodSeconds: 10
          timeoutSeconds: 3
          failureThreshold: 3
        livenessProbe:
          exec:
            command:
            - python
            - -c
            - import os,time; assert time.time()-os.path.getmtime('/tmp/audio-worker-heartbeat')
              < 120
          initialDelaySeconds: 30
          periodSeconds: 20
          timeoutSeconds: 3
          failureThreshold: 3
