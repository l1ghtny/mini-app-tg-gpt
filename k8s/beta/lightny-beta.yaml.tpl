apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: tg-mini-beta-redis
  namespace: gpt
spec:
  accessModes:
    - ReadWriteOnce
  storageClassName: longhorn
  resources:
    requests:
      storage: 1Gi
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: tg-mini-beta-redis
  namespace: gpt
  labels:
    app: tg-mini-beta-redis
spec:
  replicas: 1
  selector:
    matchLabels:
      app: tg-mini-beta-redis
  template:
    metadata:
      labels:
        app: tg-mini-beta-redis
    spec:
      automountServiceAccountToken: false
      containers:
        - name: redis
          image: redis:7.4-alpine
          imagePullPolicy: IfNotPresent
          command: ["sh", "-c"]
          args:
            - exec redis-server --appendonly yes --requirepass "$REDIS_PASSWORD"
          env:
            - name: REDIS_PASSWORD
              valueFrom:
                secretKeyRef:
                  name: tg-mini-beta-redis-auth
                  key: REDIS_PASSWORD
          ports:
            - name: redis
              containerPort: 6379
          readinessProbe:
            exec:
              command:
                - sh
                - -c
                - redis-cli -a "$REDIS_PASSWORD" ping | grep -q PONG
            initialDelaySeconds: 3
            periodSeconds: 10
          livenessProbe:
            exec:
              command:
                - sh
                - -c
                - redis-cli -a "$REDIS_PASSWORD" ping | grep -q PONG
            initialDelaySeconds: 10
            periodSeconds: 20
          resources:
            requests:
              cpu: 25m
              memory: 64Mi
            limits:
              cpu: 250m
              memory: 256Mi
          volumeMounts:
            - name: data
              mountPath: /data
      volumes:
        - name: data
          persistentVolumeClaim:
            claimName: tg-mini-beta-redis
---
apiVersion: v1
kind: Service
metadata:
  name: tg-mini-beta-redis
  namespace: gpt
spec:
  selector:
    app: tg-mini-beta-redis
  ports:
    - name: redis
      port: 6379
      targetPort: redis
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: tg-mini-beta-backend
  namespace: gpt
  labels:
    app: tg-mini-beta-backend
spec:
  replicas: 1
  revisionHistoryLimit: 5
  strategy:
    type: RollingUpdate
    rollingUpdate:
      maxSurge: 1
      maxUnavailable: 0
  selector:
    matchLabels:
      app: tg-mini-beta-backend
  template:
    metadata:
      labels:
        app: tg-mini-beta-backend
    spec:
      automountServiceAccountToken: false
      containers:
        - name: api
          image: localhost:32000/tg-mini-app-backend:__BACKEND_TAG__
          imagePullPolicy: Always
          env:
            - name: HTTPS_PROXY
              value: socks5://warp-proxy:1080
            - name: HTTP_PROXY
              value: socks5://warp-proxy:1080
            - name: VOICE_TRANSCRIPTION_ENABLED
              value: "true"
            - name: VOICE_TRANSCRIPTION_MODEL
              value: gpt-transcribe
          envFrom:
            - secretRef:
                name: backend-beta-env
          ports:
            - name: http
              containerPort: 8000
          startupProbe:
            httpGet:
              path: /health/live
              port: http
              httpHeaders:
                - name: Host
                  value: 127.0.0.1
            periodSeconds: 5
            failureThreshold: 30
          readinessProbe:
            httpGet:
              path: /health/ready
              port: http
              httpHeaders:
                - name: Host
                  value: 127.0.0.1
            periodSeconds: 10
            timeoutSeconds: 2
            failureThreshold: 3
          livenessProbe:
            httpGet:
              path: /health/live
              port: http
              httpHeaders:
                - name: Host
                  value: 127.0.0.1
            initialDelaySeconds: 20
            periodSeconds: 20
            timeoutSeconds: 2
            failureThreshold: 3
          resources:
            requests:
              cpu: 200m
              memory: 512Mi
            limits:
              cpu: "1"
              memory: 1Gi
---
apiVersion: v1
kind: Service
metadata:
  name: tg-mini-beta-backend
  namespace: gpt
spec:
  selector:
    app: tg-mini-beta-backend
  ports:
    - name: http
      port: 80
      targetPort: http
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: tg-mini-beta-work-run-worker
  namespace: gpt
  labels:
    app: tg-mini-beta-work-run-worker
spec:
  replicas: 1
  revisionHistoryLimit: 5
  strategy:
    type: Recreate
  selector:
    matchLabels:
      app: tg-mini-beta-work-run-worker
  template:
    metadata:
      labels:
        app: tg-mini-beta-work-run-worker
    spec:
      automountServiceAccountToken: false
      terminationGracePeriodSeconds: 30
      containers:
        - name: worker
          image: localhost:32000/tg-mini-app-backend:__BACKEND_TAG__
          imagePullPolicy: Always
          command:
            - python
            - -m
            - fastapi
            - run
            - work_run_worker_app.py
            - --host
            - 0.0.0.0
            - --port
            - "8000"
          envFrom:
            - secretRef:
                name: backend-beta-env
          ports:
            - name: health
              containerPort: 8000
          startupProbe:
            httpGet:
              path: /health/live
              port: health
            periodSeconds: 5
            failureThreshold: 30
          readinessProbe:
            httpGet:
              path: /health/ready
              port: health
            periodSeconds: 10
            timeoutSeconds: 2
            failureThreshold: 3
          livenessProbe:
            httpGet:
              path: /health/live
              port: health
            initialDelaySeconds: 20
            periodSeconds: 20
            timeoutSeconds: 2
            failureThreshold: 3
          resources:
            requests:
              cpu: 100m
              memory: 256Mi
            limits:
              cpu: "1"
              memory: 1Gi
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: tg-mini-beta-frontend
  namespace: gpt
  labels:
    app: tg-mini-beta-frontend
spec:
  replicas: 1
  revisionHistoryLimit: 5
  strategy:
    type: RollingUpdate
    rollingUpdate:
      maxSurge: 1
      maxUnavailable: 0
  selector:
    matchLabels:
      app: tg-mini-beta-frontend
  template:
    metadata:
      labels:
        app: tg-mini-beta-frontend
    spec:
      automountServiceAccountToken: false
      containers:
        - name: web
          image: localhost:32000/tg-mini-frontend-new:__FRONTEND_TAG__
          imagePullPolicy: Always
          ports:
            - name: http
              containerPort: 80
          readinessProbe:
            httpGet:
              path: /health.json
              port: http
            initialDelaySeconds: 3
            periodSeconds: 10
            timeoutSeconds: 2
          livenessProbe:
            httpGet:
              path: /health.json
              port: http
            initialDelaySeconds: 10
            periodSeconds: 20
            timeoutSeconds: 2
          resources:
            requests:
              cpu: 50m
              memory: 96Mi
            limits:
              cpu: 500m
              memory: 384Mi
---
apiVersion: v1
kind: Service
metadata:
  name: tg-mini-beta-frontend
  namespace: gpt
spec:
  selector:
    app: tg-mini-beta-frontend
  ports:
    - name: http
      port: 80
      targetPort: http
---
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: tg-mini-beta
  namespace: gpt
  annotations:
    cert-manager.io/cluster-issuer: letsencrypt-prod
    kubernetes.io/ingress.class: public-awg
    nginx.ingress.kubernetes.io/proxy-body-size: 100m
    nginx.ingress.kubernetes.io/proxy-buffering: "off"
    nginx.ingress.kubernetes.io/proxy-http-version: "1.1"
    nginx.ingress.kubernetes.io/proxy-read-timeout: "3600"
    nginx.ingress.kubernetes.io/proxy-send-timeout: "3600"
spec:
  ingressClassName: public-awg
  rules:
    - host: beta.app.lightny.ru
      http:
        paths:
          - path: /health/live
            pathType: Exact
            backend:
              service:
                name: tg-mini-beta-backend
                port:
                  number: 80
          - path: /health/ready
            pathType: Exact
            backend:
              service:
                name: tg-mini-beta-backend
                port:
                  number: 80
          - path: /api
            pathType: Prefix
            backend:
              service:
                name: tg-mini-beta-backend
                port:
                  number: 80
          - path: /
            pathType: Prefix
            backend:
              service:
                name: tg-mini-beta-frontend
                port:
                  number: 80
  tls:
    - hosts:
        - beta.app.lightny.ru
      secretName: tg-mini-beta-tls
---
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: tg-mini-beta-web
  namespace: gpt
spec:
  podSelector:
    matchExpressions:
      - key: app
        operator: In
        values:
          - tg-mini-beta-backend
          - tg-mini-beta-frontend
  policyTypes:
    - Ingress
  ingress:
    - from:
        - namespaceSelector:
            matchLabels:
              kubernetes.io/metadata.name: ingress
          podSelector:
            matchLabels:
              app.kubernetes.io/name: nginx-ingress-awg
              app.kubernetes.io/component: controller
---
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: tg-mini-beta-redis
  namespace: gpt
spec:
  podSelector:
    matchLabels:
      app: tg-mini-beta-redis
  policyTypes:
    - Ingress
  ingress:
    - from:
        - podSelector:
            matchLabels:
              app: tg-mini-beta-backend
        - podSelector:
            matchLabels:
              app: tg-mini-beta-work-run-worker
      ports:
        - protocol: TCP
          port: 6379
