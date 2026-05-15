package controller

import (
    "context"
    "testing"

    appsv1 "k8s.io/api/apps/v1"
    corev1 "k8s.io/api/core/v1"
    apierrors "k8s.io/apimachinery/pkg/api/errors"
    metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
    "k8s.io/apimachinery/pkg/runtime"
    "k8s.io/apimachinery/pkg/types"
    "k8s.io/apimachinery/pkg/apis/meta/v1/unstructured"
    ctrl "sigs.k8s.io/controller-runtime"
    "sigs.k8s.io/controller-runtime/pkg/client/fake"

    gatewayv1alpha1 "github.com/honeybury/boostasiodemo/operator/boostgateway-operator/api/v1alpha1"
)

func TestReconcileCreatesManagedResources(t *testing.T) {
    scheme := newTestScheme(t)
    managementPort := int32(18080)
    matchReplicas := int32(3)
    disabled := false

    cluster := &gatewayv1alpha1.BoostGatewayCluster{
        ObjectMeta: metav1.ObjectMeta{
            Name:      "demo",
            Namespace: "default",
        },
        Spec: gatewayv1alpha1.BoostGatewayClusterSpec{
            PullPolicy: corev1.PullIfNotPresent,
            TLS: gatewayv1alpha1.TLSConfig{
                Enabled:              true,
                SecretName:           "demo-tls",
                ManagedByCertManager: true,
                CertManagerIssuer:    "shared-ca",
            },
            Gateway: gatewayv1alpha1.ComponentSpec{
                Image:          "gateway",
                Port:           9201,
                ManagementPort: &managementPort,
            },
            Login: gatewayv1alpha1.ComponentSpec{Enabled: &disabled},
            Room: gatewayv1alpha1.ComponentSpec{Enabled: &disabled},
            Battle: gatewayv1alpha1.ComponentSpec{Enabled: &disabled},
            Match: gatewayv1alpha1.ComponentSpec{
                Image:    "match",
                Port:     9304,
                Replicas: &matchReplicas,
            },
            Leaderboard: gatewayv1alpha1.ComponentSpec{Enabled: &disabled},
        },
    }

    client := fake.NewClientBuilder().
        WithScheme(scheme).
        WithStatusSubresource(&gatewayv1alpha1.BoostGatewayCluster{}).
        WithObjects(cluster).
        Build()

    reconciler := &BoostGatewayClusterReconciler{
        Client: client,
        Scheme: scheme,
    }

    _, err := reconciler.Reconcile(context.Background(), ctrl.Request{
        NamespacedName: types.NamespacedName{Name: cluster.Name, Namespace: cluster.Namespace},
    })
    if err != nil {
        t.Fatalf("reconcile failed: %v", err)
    }

    var gatewayDeployment appsv1.Deployment
    if err := client.Get(context.Background(), types.NamespacedName{
        Namespace: "default",
        Name:      "demo-gateway",
    }, &gatewayDeployment); err != nil {
        t.Fatalf("gateway deployment missing: %v", err)
    }
    if len(gatewayDeployment.Spec.Template.Spec.Containers) != 1 {
        t.Fatalf("expected 1 container, got %d", len(gatewayDeployment.Spec.Template.Spec.Containers))
    }
    gatewayContainer := gatewayDeployment.Spec.Template.Spec.Containers[0]
    if len(gatewayContainer.EnvFrom) != 1 || gatewayContainer.EnvFrom[0].ConfigMapRef == nil {
        t.Fatalf("gateway container missing ConfigMap env")
    }
    if gatewayContainer.EnvFrom[0].ConfigMapRef.Name != "demo-gateway-config" {
        t.Fatalf("unexpected gateway configmap ref: %s", gatewayContainer.EnvFrom[0].ConfigMapRef.Name)
    }
    if len(gatewayContainer.VolumeMounts) != 1 || gatewayContainer.VolumeMounts[0].Name != "tls" {
        t.Fatalf("gateway container missing tls mount")
    }

    var gatewayConfig corev1.ConfigMap
    if err := client.Get(context.Background(), types.NamespacedName{
        Namespace: "default",
        Name:      "demo-gateway-config",
    }, &gatewayConfig); err != nil {
        t.Fatalf("gateway configmap missing: %v", err)
    }
    if gatewayConfig.Data["SERVICE_PORT"] != "9201" {
        t.Fatalf("unexpected gateway SERVICE_PORT: %s", gatewayConfig.Data["SERVICE_PORT"])
    }
    if gatewayConfig.Data["MANAGEMENT_PORT"] != "18080" {
        t.Fatalf("unexpected gateway MANAGEMENT_PORT: %s", gatewayConfig.Data["MANAGEMENT_PORT"])
    }
    if gatewayConfig.Data["TLS_SECRET_NAME"] != "demo-tls" {
        t.Fatalf("unexpected gateway TLS secret: %s", gatewayConfig.Data["TLS_SECRET_NAME"])
    }

    var tlsSecret corev1.Secret
    if err := client.Get(context.Background(), types.NamespacedName{
        Namespace: "default",
        Name:      "demo-tls",
    }, &tlsSecret); err != nil {
        t.Fatalf("tls secret missing: %v", err)
    }
    if tlsSecret.Type != corev1.SecretTypeTLS {
        t.Fatalf("unexpected secret type: %s", tlsSecret.Type)
    }
    if tlsSecret.Annotations["gateway.boost.io/tls-mode"] != "cert-manager" {
        t.Fatalf("unexpected tls mode: %q", tlsSecret.Annotations["gateway.boost.io/tls-mode"])
    }
    if tlsSecret.Annotations["cert-manager.io/cluster-issuer"] != "shared-ca" {
        t.Fatalf("unexpected cert-manager issuer annotation: %q", tlsSecret.Annotations["cert-manager.io/cluster-issuer"])
    }

    cert := &unstructured.Unstructured{}
    cert.SetAPIVersion("cert-manager.io/v1")
    cert.SetKind("Certificate")
    if err := client.Get(context.Background(), types.NamespacedName{
        Namespace: "default",
        Name:      "demo-tls",
    }, cert); err != nil {
        t.Fatalf("certificate missing: %v", err)
    }
    issuerRef, found, err := unstructured.NestedMap(cert.Object, "spec", "issuerRef")
    if err != nil || !found {
        t.Fatalf("certificate issuerRef missing: %v", err)
    }
    if issuerRef["name"] != "shared-ca" {
        t.Fatalf("unexpected issuerRef name: %v", issuerRef["name"])
    }

    var matchSet appsv1.StatefulSet
    if err := client.Get(context.Background(), types.NamespacedName{
        Namespace: "default",
        Name:      "demo-match",
    }, &matchSet); err != nil {
        t.Fatalf("match statefulset missing: %v", err)
    }
    if matchSet.Spec.ServiceName != "demo-match" {
        t.Fatalf("unexpected service name: %s", matchSet.Spec.ServiceName)
    }
    if matchSet.Spec.Replicas == nil || *matchSet.Spec.Replicas != 3 {
        t.Fatalf("unexpected match replicas: %v", matchSet.Spec.Replicas)
    }
    matchContainer := matchSet.Spec.Template.Spec.Containers[0]
    assertEnvPresent(t, matchContainer.Env, "RAFT_NODE_ID")
    assertEnvValue(t, matchContainer.Env, "RAFT_SERVICE_NAME", "demo-match")
    assertEnvValue(t, matchContainer.Env, "RAFT_REPLICAS", "3")
    assertEnvValue(t, matchContainer.Env, "BOOST_COMPONENT_NAME", "match")

    var matchService corev1.Service
    if err := client.Get(context.Background(), types.NamespacedName{
        Namespace: "default",
        Name:      "demo-match",
    }, &matchService); err != nil {
        t.Fatalf("match service missing: %v", err)
    }
    if matchService.Spec.ClusterIP != corev1.ClusterIPNone {
        t.Fatalf("expected headless match service, got %q", matchService.Spec.ClusterIP)
    }
    if !matchService.Spec.PublishNotReadyAddresses {
        t.Fatalf("expected PublishNotReadyAddresses=true for match service")
    }

    var refreshed gatewayv1alpha1.BoostGatewayCluster
    if err := client.Get(context.Background(), types.NamespacedName{
        Namespace: "default",
        Name:      "demo",
    }, &refreshed); err != nil {
        t.Fatalf("reload cluster: %v", err)
    }
    if refreshed.Status.DesiredReplicas != 4 {
        t.Fatalf("unexpected desired replicas: %d", refreshed.Status.DesiredReplicas)
    }
    if refreshed.Status.Phase != "Progressing" {
        t.Fatalf("unexpected phase: %q", refreshed.Status.Phase)
    }
    if len(refreshed.Status.Components) != 2 {
        t.Fatalf("expected 2 component statuses, got %d", len(refreshed.Status.Components))
    }
    foundGateway := false
    foundMatch := false
    for _, component := range refreshed.Status.Components {
        if component.Name == "gateway" {
            foundGateway = true
            if component.Kind != "Deployment" {
                t.Fatalf("unexpected gateway kind: %q", component.Kind)
            }
            if component.DesiredReplicas != 1 {
                t.Fatalf("unexpected gateway desired replicas: %d", component.DesiredReplicas)
            }
        }
        if component.Name == "match" {
            foundMatch = true
            if component.Kind != "StatefulSet" {
                t.Fatalf("unexpected match kind: %q", component.Kind)
            }
            if component.DesiredReplicas != 3 {
                t.Fatalf("unexpected match desired replicas: %d", component.DesiredReplicas)
            }
        }
    }
    if !foundGateway || !foundMatch {
        t.Fatalf("missing expected component statuses: gateway=%v match=%v", foundGateway, foundMatch)
    }
    if len(refreshed.Status.Conditions) < 4 {
        t.Fatalf("expected at least 4 status conditions, got %d", len(refreshed.Status.Conditions))
    }
    foundReady := false
    foundProgressing := false
    foundDegraded := false
    foundTLSReady := false
    for _, condition := range refreshed.Status.Conditions {
        switch condition.Type {
        case "Ready":
            foundReady = true
            if condition.Status != metav1.ConditionFalse {
                t.Fatalf("expected Ready=False during rollout, got %s", condition.Status)
            }
        case "Progressing":
            foundProgressing = true
            if condition.Status != metav1.ConditionTrue {
                t.Fatalf("expected Progressing=True during rollout, got %s", condition.Status)
            }
        case "Degraded":
            foundDegraded = true
        case "TLSReady":
            foundTLSReady = true
        }
    }
    if !foundReady || !foundProgressing || !foundDegraded || !foundTLSReady {
        t.Fatalf("missing expected conditions: ready=%v progressing=%v degraded=%v tls=%v",
            foundReady, foundProgressing, foundDegraded, foundTLSReady)
    }
}

func TestReconcileDeletesDisabledComponentResources(t *testing.T) {
    scheme := newTestScheme(t)
    enabled := false

    cluster := &gatewayv1alpha1.BoostGatewayCluster{
        ObjectMeta: metav1.ObjectMeta{
            Name:      "demo",
            Namespace: "default",
        },
        Spec: gatewayv1alpha1.BoostGatewayClusterSpec{
            Login: gatewayv1alpha1.ComponentSpec{
                Enabled: &enabled,
                Image:   "login",
                Port:    9202,
            },
        },
    }

    deployment := &appsv1.Deployment{ObjectMeta: metav1.ObjectMeta{
        Name:      "demo-login",
        Namespace: "default",
    }}
    service := &corev1.Service{ObjectMeta: metav1.ObjectMeta{
        Name:      "demo-login",
        Namespace: "default",
    }}
    configMap := &corev1.ConfigMap{ObjectMeta: metav1.ObjectMeta{
        Name:      "demo-login-config",
        Namespace: "default",
    }}

    client := fake.NewClientBuilder().
        WithScheme(scheme).
        WithStatusSubresource(&gatewayv1alpha1.BoostGatewayCluster{}).
        WithObjects(cluster, deployment, service, configMap).
        Build()

    reconciler := &BoostGatewayClusterReconciler{
        Client: client,
        Scheme: scheme,
    }

    _, err := reconciler.Reconcile(context.Background(), ctrl.Request{
        NamespacedName: types.NamespacedName{Name: cluster.Name, Namespace: cluster.Namespace},
    })
    if err != nil {
        t.Fatalf("reconcile failed: %v", err)
    }

    if err := client.Get(context.Background(), types.NamespacedName{
        Namespace: "default",
        Name:      "demo-login",
    }, &appsv1.Deployment{}); !apierrors.IsNotFound(err) {
        t.Fatalf("expected login deployment deleted, got err=%v", err)
    }
    if err := client.Get(context.Background(), types.NamespacedName{
        Namespace: "default",
        Name:      "demo-login",
    }, &corev1.Service{}); !apierrors.IsNotFound(err) {
        t.Fatalf("expected login service deleted, got err=%v", err)
    }
    if err := client.Get(context.Background(), types.NamespacedName{
        Namespace: "default",
        Name:      "demo-login-config",
    }, &corev1.ConfigMap{}); !apierrors.IsNotFound(err) {
        t.Fatalf("expected login configmap deleted, got err=%v", err)
    }
}

func TestSummarizeDeploymentMarksDegradedWhenRolloutLags(t *testing.T) {
    replicas := int32(3)
    deployment := &appsv1.Deployment{
        ObjectMeta: metav1.ObjectMeta{
            Name:       "demo-gateway",
            Generation: 5,
        },
        Spec: appsv1.DeploymentSpec{
            Replicas: &replicas,
        },
        Status: appsv1.DeploymentStatus{
            ObservedGeneration: 4,
            ReadyReplicas:      1,
            UpdatedReplicas:    1,
            AvailableReplicas:  1,
        },
    }

    rollout := summarizeDeployment("gateway", deployment)
    if rollout.ready {
        t.Fatalf("expected rollout not ready")
    }
    if !rollout.degraded {
        t.Fatalf("expected rollout degraded")
    }
    if rollout.degradedReason == "" {
        t.Fatalf("expected degraded reason")
    }
}

func newTestScheme(t *testing.T) *runtime.Scheme {
    t.Helper()

    scheme := runtime.NewScheme()
    if err := corev1.AddToScheme(scheme); err != nil {
        t.Fatalf("add core scheme: %v", err)
    }
    if err := appsv1.AddToScheme(scheme); err != nil {
        t.Fatalf("add apps scheme: %v", err)
    }
    if err := gatewayv1alpha1.AddToScheme(scheme); err != nil {
        t.Fatalf("add gateway scheme: %v", err)
    }
    return scheme
}

func assertEnvPresent(t *testing.T, env []corev1.EnvVar, name string) {
    t.Helper()
    for _, item := range env {
        if item.Name == name {
            return
        }
    }
    t.Fatalf("missing env %s", name)
}

func assertEnvValue(t *testing.T, env []corev1.EnvVar, name string, expected string) {
    t.Helper()
    for _, item := range env {
        if item.Name == name {
            if item.Value != expected {
                t.Fatalf("env %s mismatch: got %q want %q", name, item.Value, expected)
            }
            return
        }
    }
    t.Fatalf("missing env %s", name)
}
