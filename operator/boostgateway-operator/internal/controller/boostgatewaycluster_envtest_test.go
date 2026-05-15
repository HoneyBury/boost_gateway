package controller

import (
	"context"
	"os"
	"path/filepath"
	"testing"

	appsv1 "k8s.io/api/apps/v1"
	corev1 "k8s.io/api/core/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/types"
	ctrl "sigs.k8s.io/controller-runtime"
	"sigs.k8s.io/controller-runtime/pkg/client"
	"sigs.k8s.io/controller-runtime/pkg/envtest"

	gatewayv1alpha1 "github.com/honeybury/boostasiodemo/operator/boostgateway-operator/api/v1alpha1"
)

func TestEnvtestReconcileCreatesManagedResources(t *testing.T) {
	if os.Getenv("KUBEBUILDER_ASSETS") == "" {
		t.Skip("KUBEBUILDER_ASSETS is not set")
	}

	scheme := newTestScheme(t)
	crdPath, err := filepath.Abs(filepath.Join("..", "..", "config", "crd", "bases"))
	if err != nil {
		t.Fatalf("resolve crd path: %v", err)
	}

	testEnv := &envtest.Environment{
		CRDDirectoryPaths: []string{crdPath},
	}
	cfg, err := testEnv.Start()
	if err != nil {
		t.Fatalf("start envtest: %v", err)
	}
	defer func() {
		if stopErr := testEnv.Stop(); stopErr != nil {
			t.Fatalf("stop envtest: %v", stopErr)
		}
	}()

	k8sClient, err := client.New(cfg, client.Options{Scheme: scheme})
	if err != nil {
		t.Fatalf("create client: %v", err)
	}

	cluster := &gatewayv1alpha1.BoostGatewayCluster{
		ObjectMeta: metav1.ObjectMeta{
			Name:      "demo",
			Namespace: "default",
		},
		Spec: gatewayv1alpha1.BoostGatewayClusterSpec{
			Gateway: gatewayv1alpha1.ComponentSpec{
				Image: "gateway",
				Port:  9201,
			},
			Match: gatewayv1alpha1.ComponentSpec{
				Image: "match",
				Port:  9304,
			},
		},
	}
	if err := k8sClient.Create(context.Background(), cluster); err != nil {
		t.Fatalf("create cluster: %v", err)
	}

	reconciler := &BoostGatewayClusterReconciler{
		Client: k8sClient,
		Scheme: scheme,
	}
	if _, err := reconciler.Reconcile(context.Background(), ctrl.Request{
		NamespacedName: types.NamespacedName{Name: cluster.Name, Namespace: cluster.Namespace},
	}); err != nil {
		t.Fatalf("reconcile failed: %v", err)
	}

	var gatewayDeployment appsv1.Deployment
	if err := k8sClient.Get(context.Background(), types.NamespacedName{
		Namespace: "default",
		Name:      "demo-gateway",
	}, &gatewayDeployment); err != nil {
		t.Fatalf("gateway deployment missing: %v", err)
	}

	var matchSet appsv1.StatefulSet
	if err := k8sClient.Get(context.Background(), types.NamespacedName{
		Namespace: "default",
		Name:      "demo-match",
	}, &matchSet); err != nil {
		t.Fatalf("match statefulset missing: %v", err)
	}

	var matchService corev1.Service
	if err := k8sClient.Get(context.Background(), types.NamespacedName{
		Namespace: "default",
		Name:      "demo-match",
	}, &matchService); err != nil {
		t.Fatalf("match service missing: %v", err)
	}
	if matchService.Spec.ClusterIP != corev1.ClusterIPNone {
		t.Fatalf("expected headless match service, got %q", matchService.Spec.ClusterIP)
	}

	var refreshed gatewayv1alpha1.BoostGatewayCluster
	if err := k8sClient.Get(context.Background(), types.NamespacedName{
		Namespace: "default",
		Name:      "demo",
	}, &refreshed); err != nil {
		t.Fatalf("reload cluster: %v", err)
	}
	if refreshed.Status.Phase != "Running" {
		t.Fatalf("unexpected status phase: %q", refreshed.Status.Phase)
	}
}
