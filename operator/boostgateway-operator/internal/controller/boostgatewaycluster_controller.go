package controller

import (
    "context"
    "fmt"
    "reflect"
    "strconv"
    "strings"

    appsv1 "k8s.io/api/apps/v1"
    corev1 "k8s.io/api/core/v1"
    apierrors "k8s.io/apimachinery/pkg/api/errors"
    unstructuredv1 "k8s.io/apimachinery/pkg/apis/meta/v1/unstructured"
    metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
    "k8s.io/apimachinery/pkg/runtime"
    "k8s.io/apimachinery/pkg/types"
    "k8s.io/apimachinery/pkg/util/intstr"
    ctrl "sigs.k8s.io/controller-runtime"
    "sigs.k8s.io/controller-runtime/pkg/client"
    "sigs.k8s.io/controller-runtime/pkg/controller/controllerutil"
    "sigs.k8s.io/controller-runtime/pkg/log"

    gatewayv1alpha1 "github.com/honeybury/boostasiodemo/operator/boostgateway-operator/api/v1alpha1"
)

type BoostGatewayClusterReconciler struct {
    client.Client
    Scheme *runtime.Scheme
}

const certManagerIssuerAnnotation = "cert-manager.io/cluster-issuer"

type managedComponent struct {
    Name string
    Spec gatewayv1alpha1.ComponentSpec
}

type componentRolloutStatus struct {
    status gatewayv1alpha1.ComponentStatus
    ready  bool
    degraded bool
    degradedReason string
}

func (m managedComponent) usesStatefulSet() bool {
    return m.Name == "match" || m.Name == "leaderboard"
}

func (r *BoostGatewayClusterReconciler) Reconcile(ctx context.Context, req ctrl.Request) (ctrl.Result, error) {
    logger := log.FromContext(ctx)

    var cluster gatewayv1alpha1.BoostGatewayCluster
    if err := r.Get(ctx, req.NamespacedName, &cluster); err != nil {
        return ctrl.Result{}, client.IgnoreNotFound(err)
    }

    components := []managedComponent{
        {Name: "gateway", Spec: cluster.Spec.Gateway},
        {Name: "login", Spec: cluster.Spec.Login},
        {Name: "room", Spec: cluster.Spec.Room},
        {Name: "battle", Spec: cluster.Spec.Battle},
        {Name: "match", Spec: cluster.Spec.Match},
        {Name: "leaderboard", Spec: cluster.Spec.Leaderboard},
    }

    var totalReady int32
    var totalDesired int32
    componentStatuses := make([]gatewayv1alpha1.ComponentStatus, 0, len(components))
    allRolloutsReady := true
    degradedReasons := make([]string, 0)
    if err := r.reconcileClusterTLSSecret(ctx, &cluster); err != nil {
        return ctrl.Result{}, err
    }
    for _, component := range components {
        if !isEnabled(component.Spec) {
            if err := r.deleteComponent(ctx, cluster.Namespace, cluster.Name, component.Name); err != nil {
                return ctrl.Result{}, err
            }
            if err := r.deleteComponentConfig(ctx, cluster.Namespace, cluster.Name, component.Name); err != nil {
                return ctrl.Result{}, err
            }
            continue
        }
        if err := r.reconcileComponentConfigMap(ctx, &cluster, component); err != nil {
            return ctrl.Result{}, err
        }
        if component.usesStatefulSet() {
            if err := r.reconcileStatefulSet(ctx, &cluster, component); err != nil {
                return ctrl.Result{}, err
            }
            totalDesired += replicasOrDefault(component.Spec, 1)
        } else {
            if err := r.reconcileDeployment(ctx, &cluster, component); err != nil {
                return ctrl.Result{}, err
            }
            totalDesired += replicasOrDefault(component.Spec, 1)
        }
        if err := r.reconcileService(ctx, &cluster, component); err != nil {
            return ctrl.Result{}, err
        }

        if component.usesStatefulSet() {
            var statefulSet appsv1.StatefulSet
            if err := r.Get(ctx, types.NamespacedName{
                Namespace: cluster.Namespace,
                Name:      componentResourceName(cluster.Name, component.Name),
            }, &statefulSet); err == nil {
                rollout := summarizeStatefulSet(component.Name, &statefulSet)
                componentStatuses = append(componentStatuses, rollout.status)
                totalReady += rollout.status.ReadyReplicas
                if !rollout.ready {
                    allRolloutsReady = false
                }
                if rollout.degraded {
                    degradedReasons = append(degradedReasons, rollout.degradedReason)
                }
            } else if !apierrors.IsNotFound(err) {
                return ctrl.Result{}, err
            }
        } else {
            var deployment appsv1.Deployment
            if err := r.Get(ctx, types.NamespacedName{
                Namespace: cluster.Namespace,
                Name:      componentResourceName(cluster.Name, component.Name),
            }, &deployment); err == nil {
                rollout := summarizeDeployment(component.Name, &deployment)
                componentStatuses = append(componentStatuses, rollout.status)
                totalReady += rollout.status.ReadyReplicas
                if !rollout.ready {
                    allRolloutsReady = false
                }
                if rollout.degraded {
                    degradedReasons = append(degradedReasons, rollout.degradedReason)
                }
            } else if !apierrors.IsNotFound(err) {
                return ctrl.Result{}, err
            }
        }
    }

    readyStatus := metav1.ConditionFalse
    progressingStatus := metav1.ConditionTrue
    degradedStatus := metav1.ConditionFalse
    phase := "Progressing"
    readyReason := "WaitingForReplicas"
    readyMessage := fmt.Sprintf("Ready replicas %d/%d", totalReady, totalDesired)
    progressingReason := "RolloutInProgress"
    progressingMessage := fmt.Sprintf("Rollout progressing: ready replicas %d/%d.", totalReady, totalDesired)
    degradedReason := "NoIssuesDetected"
    degradedMessage := "No degraded components detected."

    if totalDesired == 0 || (totalReady >= totalDesired && allRolloutsReady) {
        readyStatus = metav1.ConditionTrue
        progressingStatus = metav1.ConditionFalse
        phase = "Running"
        readyReason = "ComponentsReady"
        readyMessage = "All enabled components are ready."
        progressingReason = "RolloutComplete"
        progressingMessage = "No rollout in progress."
    } else if len(degradedReasons) > 0 {
        degradedStatus = metav1.ConditionTrue
        degradedReason = "RolloutHealthIssue"
        degradedMessage = strings.Join(degradedReasons, "; ")
    } else if totalReady == 0 && totalDesired > 0 {
        degradedStatus = metav1.ConditionTrue
        degradedReason = "NoReadyReplicas"
        degradedMessage = "No enabled component currently reports ready replicas."
    }

    desiredStatus := gatewayv1alpha1.BoostGatewayClusterStatus{
        Phase:           phase,
        ReadyReplicas:   totalReady,
        DesiredReplicas: totalDesired,
        Components:      componentStatuses,
        Conditions: []metav1.Condition{
            {
                Type:               "Ready",
                Status:             readyStatus,
                Reason:             readyReason,
                Message:            readyMessage,
                LastTransitionTime: metav1.Now(),
            },
            {
                Type:               "Progressing",
                Status:             progressingStatus,
                Reason:             progressingReason,
                Message:            progressingMessage,
                LastTransitionTime: metav1.Now(),
            },
            {
                Type:               "Degraded",
                Status:             degradedStatus,
                Reason:             degradedReason,
                Message:            degradedMessage,
                LastTransitionTime: metav1.Now(),
            },
            {
                Type:               "TLSReady",
                Status:             tlsConditionStatus(cluster.Spec.TLS),
                Reason:             tlsConditionReason(cluster.Spec.TLS),
                Message:            tlsConditionMessage(cluster.Spec.TLS),
                LastTransitionTime: metav1.Now(),
            },
        },
    }
    if !reflect.DeepEqual(cluster.Status, desiredStatus) {
        cluster.Status = desiredStatus
        if err := r.Status().Update(ctx, &cluster); err != nil {
            return ctrl.Result{}, err
        }
    }

    logger.Info("reconciled BoostGatewayCluster", "name", cluster.Name, "readyReplicas", totalReady)
    return ctrl.Result{}, nil
}

func (r *BoostGatewayClusterReconciler) reconcileDeployment(
    ctx context.Context,
    cluster *gatewayv1alpha1.BoostGatewayCluster,
    component managedComponent,
) error {
    name := componentResourceName(cluster.Name, component.Name)
    labels := componentLabels(cluster.Name, component.Name)
    replicas := replicasOrDefault(component.Spec, 1)
    image := imageFor(cluster.Spec, component.Spec)

    deployment := &appsv1.Deployment{
        ObjectMeta: metav1.ObjectMeta{
            Name:      name,
            Namespace: cluster.Namespace,
        },
    }

    _, err := controllerutil.CreateOrUpdate(ctx, r.Client, deployment, func() error {
        deployment.Labels = labels
        deployment.Spec.Replicas = &replicas
        deployment.Spec.Selector = &metav1.LabelSelector{MatchLabels: labels}
        deployment.Spec.Template.ObjectMeta.Labels = labels
        deployment.Spec.Template.Spec.Containers = []corev1.Container{
            buildComponentContainer(cluster, component, image),
        }
        deployment.Spec.Template.Spec.Volumes = componentVolumes(cluster)
        return controllerutil.SetControllerReference(cluster, deployment, r.Scheme)
    })
    return err
}

func (r *BoostGatewayClusterReconciler) reconcileService(
    ctx context.Context,
    cluster *gatewayv1alpha1.BoostGatewayCluster,
    component managedComponent,
) error {
    name := componentResourceName(cluster.Name, component.Name)
    labels := componentLabels(cluster.Name, component.Name)
    service := &corev1.Service{
        ObjectMeta: metav1.ObjectMeta{
            Name:      name,
            Namespace: cluster.Namespace,
        },
    }

    _, err := controllerutil.CreateOrUpdate(ctx, r.Client, service, func() error {
        service.Labels = labels
        service.Spec.Selector = labels
        if component.usesStatefulSet() {
            service.Spec.ClusterIP = corev1.ClusterIPNone
            service.Spec.PublishNotReadyAddresses = true
        } else {
            service.Spec.ClusterIP = ""
            service.Spec.PublishNotReadyAddresses = false
        }
        service.Spec.Ports = servicePorts(component.Spec)
        return controllerutil.SetControllerReference(cluster, service, r.Scheme)
    })
    return err
}

func (r *BoostGatewayClusterReconciler) reconcileComponentConfigMap(
    ctx context.Context,
    cluster *gatewayv1alpha1.BoostGatewayCluster,
    component managedComponent,
) error {
    configMap := &corev1.ConfigMap{
        ObjectMeta: metav1.ObjectMeta{
            Name:      componentConfigMapName(cluster.Name, component.Name),
            Namespace: cluster.Namespace,
        },
    }

    _, err := controllerutil.CreateOrUpdate(ctx, r.Client, configMap, func() error {
        configMap.Labels = componentLabels(cluster.Name, component.Name)
        configMap.Data = componentConfigData(cluster, component)
        return controllerutil.SetControllerReference(cluster, configMap, r.Scheme)
    })
    return err
}

func (r *BoostGatewayClusterReconciler) reconcileClusterTLSSecret(
    ctx context.Context,
    cluster *gatewayv1alpha1.BoostGatewayCluster,
) error {
    if !cluster.Spec.TLS.Enabled || cluster.Spec.TLS.SecretName == "" {
        return nil
    }

    if cluster.Spec.TLS.ManagedByCertManager && cluster.Spec.TLS.CertManagerIssuer != "" {
        if err := r.reconcileCertManagerCertificate(ctx, cluster); err != nil {
            return err
        }
    }

    secret := &corev1.Secret{
        ObjectMeta: metav1.ObjectMeta{
            Name:      cluster.Spec.TLS.SecretName,
            Namespace: cluster.Namespace,
        },
    }

    _, err := controllerutil.CreateOrUpdate(ctx, r.Client, secret, func() error {
        secret.Labels = map[string]string{
            "app.kubernetes.io/name":       "boost-gateway",
            "app.kubernetes.io/part-of":    cluster.Name,
            "gateway.boost.io/clusterName": cluster.Name,
            "gateway.boost.io/managed":     "true",
        }
        if secret.Annotations == nil {
            secret.Annotations = map[string]string{}
        }
        if cluster.Spec.TLS.ManagedByCertManager && cluster.Spec.TLS.CertManagerIssuer != "" {
            secret.Annotations[certManagerIssuerAnnotation] = cluster.Spec.TLS.CertManagerIssuer
            secret.Annotations["gateway.boost.io/tls-mode"] = "cert-manager"
        } else {
            delete(secret.Annotations, certManagerIssuerAnnotation)
            secret.Annotations["gateway.boost.io/tls-mode"] = "placeholder"
        }
        secret.Type = corev1.SecretTypeTLS
        if secret.Data == nil {
            secret.Data = map[string][]byte{}
        }
        if _, ok := secret.Data["tls.crt"]; !ok {
            secret.Data["tls.crt"] = []byte{}
        }
        if _, ok := secret.Data["tls.key"]; !ok {
            secret.Data["tls.key"] = []byte{}
        }
        return controllerutil.SetControllerReference(cluster, secret, r.Scheme)
    })
    return err
}

func (r *BoostGatewayClusterReconciler) reconcileCertManagerCertificate(
    ctx context.Context,
    cluster *gatewayv1alpha1.BoostGatewayCluster,
) error {
    cert := &unstructuredv1.Unstructured{}
    cert.SetAPIVersion("cert-manager.io/v1")
    cert.SetKind("Certificate")
    cert.SetNamespace(cluster.Namespace)
    cert.SetName(cluster.Spec.TLS.SecretName)

    _, err := controllerutil.CreateOrUpdate(ctx, r.Client, cert, func() error {
        cert.SetLabels(map[string]string{
            "app.kubernetes.io/name":       "boost-gateway",
            "app.kubernetes.io/part-of":    cluster.Name,
            "gateway.boost.io/clusterName": cluster.Name,
            "gateway.boost.io/managed":     "true",
        })
        cert.Object["spec"] = map[string]any{
            "secretName": cluster.Spec.TLS.SecretName,
            "issuerRef": map[string]any{
                "kind": "ClusterIssuer",
                "name": cluster.Spec.TLS.CertManagerIssuer,
            },
            "dnsNames": []any{
                fmt.Sprintf("%s-gateway.%s.svc.cluster.local", cluster.Name, cluster.Namespace),
            },
        }
        return nil
    })
    return err
}

func (r *BoostGatewayClusterReconciler) reconcileStatefulSet(
    ctx context.Context,
    cluster *gatewayv1alpha1.BoostGatewayCluster,
    component managedComponent,
) error {
    name := componentResourceName(cluster.Name, component.Name)
    labels := componentLabels(cluster.Name, component.Name)
    replicas := replicasOrDefault(component.Spec, 1)
    image := imageFor(cluster.Spec, component.Spec)

    statefulSet := &appsv1.StatefulSet{
        ObjectMeta: metav1.ObjectMeta{
            Name:      name,
            Namespace: cluster.Namespace,
        },
    }

    _, err := controllerutil.CreateOrUpdate(ctx, r.Client, statefulSet, func() error {
        statefulSet.Labels = labels
        statefulSet.Spec.ServiceName = name
        statefulSet.Spec.Replicas = &replicas
        statefulSet.Spec.Selector = &metav1.LabelSelector{MatchLabels: labels}
        statefulSet.Spec.Template.ObjectMeta.Labels = labels
        statefulSet.Spec.Template.Spec.Containers = []corev1.Container{
            buildComponentContainer(cluster, component, image),
        }
        statefulSet.Spec.Template.Spec.Volumes = componentVolumes(cluster)
        return controllerutil.SetControllerReference(cluster, statefulSet, r.Scheme)
    })
    return err
}

func (r *BoostGatewayClusterReconciler) deleteComponent(
    ctx context.Context,
    namespace string,
    clusterName string,
    componentName string,
) error {
    name := componentResourceName(clusterName, componentName)
    deployment := &appsv1.Deployment{ObjectMeta: metav1.ObjectMeta{Name: name, Namespace: namespace}}
    if err := r.Delete(ctx, deployment); err != nil && !apierrors.IsNotFound(err) {
        return err
    }
    statefulSet := &appsv1.StatefulSet{ObjectMeta: metav1.ObjectMeta{Name: name, Namespace: namespace}}
    if err := r.Delete(ctx, statefulSet); err != nil && !apierrors.IsNotFound(err) {
        return err
    }
    service := &corev1.Service{ObjectMeta: metav1.ObjectMeta{Name: name, Namespace: namespace}}
    if err := r.Delete(ctx, service); err != nil && !apierrors.IsNotFound(err) {
        return err
    }
    return nil
}

func (r *BoostGatewayClusterReconciler) deleteComponentConfig(
    ctx context.Context,
    namespace string,
    clusterName string,
    componentName string,
) error {
    configMap := &corev1.ConfigMap{
        ObjectMeta: metav1.ObjectMeta{
            Name:      componentConfigMapName(clusterName, componentName),
            Namespace: namespace,
        },
    }
    if err := r.Delete(ctx, configMap); err != nil && !apierrors.IsNotFound(err) {
        return err
    }
    return nil
}

func (r *BoostGatewayClusterReconciler) SetupWithManager(mgr ctrl.Manager) error {
    return ctrl.NewControllerManagedBy(mgr).
        For(&gatewayv1alpha1.BoostGatewayCluster{}).
        Owns(&appsv1.Deployment{}).
        Owns(&appsv1.StatefulSet{}).
        Owns(&corev1.Service{}).
        Complete(r)
}

func componentResourceName(clusterName string, componentName string) string {
    return fmt.Sprintf("%s-%s", clusterName, componentName)
}

func componentConfigMapName(clusterName string, componentName string) string {
    return fmt.Sprintf("%s-%s-config", clusterName, componentName)
}

func componentLabels(clusterName string, componentName string) map[string]string {
    return map[string]string{
        "app.kubernetes.io/name":       "boost-gateway",
        "app.kubernetes.io/part-of":    clusterName,
        "app.kubernetes.io/component":  componentName,
        "gateway.boost.io/clusterName": clusterName,
    }
}

func isEnabled(spec gatewayv1alpha1.ComponentSpec) bool {
    return spec.Enabled == nil || *spec.Enabled
}

func replicasOrDefault(spec gatewayv1alpha1.ComponentSpec, fallback int32) int32 {
    if spec.Replicas != nil {
        return *spec.Replicas
    }
    return fallback
}

func imageFor(spec gatewayv1alpha1.BoostGatewayClusterSpec, component gatewayv1alpha1.ComponentSpec) string {
    if component.Image == "" {
        return "ghcr.io/honeybury/boost-gateway:v3.3.0"
    }
    if spec.ImageRegistry == "" {
        if spec.ImageTag == "" {
            return component.Image
        }
        return fmt.Sprintf("%s:%s", component.Image, spec.ImageTag)
    }
    if spec.ImageTag == "" {
        return fmt.Sprintf("%s/%s", spec.ImageRegistry, component.Image)
    }
    return fmt.Sprintf("%s/%s:%s", spec.ImageRegistry, component.Image, spec.ImageTag)
}

func buildComponentContainer(
    cluster *gatewayv1alpha1.BoostGatewayCluster,
    component managedComponent,
    image string,
) corev1.Container {
    env := []corev1.EnvVar{
        {Name: "BOOST_CLUSTER_NAME", Value: cluster.Name},
        {Name: "BOOST_COMPONENT_NAME", Value: component.Name},
    }
    if component.usesStatefulSet() {
        env = append(env, raftEnv(cluster, component)...)
    }

    container := corev1.Container{
        Name:            component.Name,
        Image:           image,
        ImagePullPolicy: pullPolicyOrDefault(cluster.Spec.PullPolicy),
        Ports:           containerPorts(component.Spec),
        Env:             env,
        EnvFrom: []corev1.EnvFromSource{
            {
                ConfigMapRef: &corev1.ConfigMapEnvSource{
                    LocalObjectReference: corev1.LocalObjectReference{
                        Name: componentConfigMapName(cluster.Name, component.Name),
                    },
                },
            },
        },
        Resources:      component.Spec.Resources,
        ReadinessProbe: readinessProbe(component.Spec),
        LivenessProbe:  livenessProbe(component.Spec),
    }

    if cluster.Spec.TLS.Enabled && cluster.Spec.TLS.SecretName != "" {
        container.VolumeMounts = append(container.VolumeMounts, corev1.VolumeMount{
            Name:      "tls",
            MountPath: "/etc/boostgateway/tls",
            ReadOnly:  true,
        })
    }
    return container
}

func componentVolumes(cluster *gatewayv1alpha1.BoostGatewayCluster) []corev1.Volume {
    if !cluster.Spec.TLS.Enabled || cluster.Spec.TLS.SecretName == "" {
        return nil
    }
    return []corev1.Volume{
        {
            Name: "tls",
            VolumeSource: corev1.VolumeSource{
                Secret: &corev1.SecretVolumeSource{
                    SecretName: cluster.Spec.TLS.SecretName,
                },
            },
        },
    }
}

func componentConfigData(
    cluster *gatewayv1alpha1.BoostGatewayCluster,
    component managedComponent,
) map[string]string {
    data := map[string]string{
        "BOOST_CLUSTER_NAME": cluster.Name,
        "BOOST_COMPONENT_NAME": component.Name,
        "SERVICE_PORT": strconv.FormatInt(int64(component.Spec.Port), 10),
    }
    if component.Spec.ManagementPort != nil {
        data["MANAGEMENT_PORT"] = strconv.FormatInt(int64(*component.Spec.ManagementPort), 10)
    }
    if component.usesStatefulSet() {
        data["RAFT_SERVICE_NAME"] = componentResourceName(cluster.Name, component.Name)
        data["RAFT_REPLICAS"] = strconv.FormatInt(int64(replicasOrDefault(component.Spec, 1)), 10)
        data["RAFT_STORAGE_DIR"] = fmt.Sprintf("/var/lib/boostgateway/%s/raft", component.Name)
        peers := raftEnv(cluster, component)
        for _, envVar := range peers {
            if envVar.Name == "RAFT_PEERS" {
                data["RAFT_PEERS"] = envVar.Value
                break
            }
        }
    }
    if cluster.Spec.TLS.Enabled && cluster.Spec.TLS.SecretName != "" {
        data["TLS_SECRET_NAME"] = cluster.Spec.TLS.SecretName
    }
    return data
}

func pullPolicyOrDefault(policy corev1.PullPolicy) corev1.PullPolicy {
    if policy == "" {
        return corev1.PullIfNotPresent
    }
    return policy
}

func tlsConditionStatus(tls gatewayv1alpha1.TLSConfig) metav1.ConditionStatus {
    if !tls.Enabled {
        return metav1.ConditionFalse
    }
    return metav1.ConditionTrue
}

func tlsConditionReason(tls gatewayv1alpha1.TLSConfig) string {
    if !tls.Enabled {
        return "TLSDisabled"
    }
    if tls.ManagedByCertManager && tls.CertManagerIssuer != "" {
        return "CertManagerConfigured"
    }
    return "StaticSecretConfigured"
}

func tlsConditionMessage(tls gatewayv1alpha1.TLSConfig) string {
    if !tls.Enabled {
        return "TLS is disabled."
    }
    if tls.ManagedByCertManager && tls.CertManagerIssuer != "" {
        return fmt.Sprintf("TLS secret is managed by cert-manager issuer %q.", tls.CertManagerIssuer)
    }
    return "TLS secret is reconciled as a placeholder/static secret."
}

func containerPorts(spec gatewayv1alpha1.ComponentSpec) []corev1.ContainerPort {
    ports := []corev1.ContainerPort{
        {
            Name:          "service",
            ContainerPort: spec.Port,
        },
    }
    if spec.ManagementPort != nil {
        ports = append(ports, corev1.ContainerPort{
            Name:          "management",
            ContainerPort: *spec.ManagementPort,
        })
    }
    return ports
}

func servicePorts(spec gatewayv1alpha1.ComponentSpec) []corev1.ServicePort {
    ports := []corev1.ServicePort{
        {
            Name:       "service",
            Port:       spec.Port,
            TargetPort: intstrFromInt32(spec.Port),
        },
    }
    if spec.ManagementPort != nil {
        ports = append(ports, corev1.ServicePort{
            Name:       "management",
            Port:       *spec.ManagementPort,
            TargetPort: intstrFromInt32(*spec.ManagementPort),
        })
    }
    return ports
}

func readinessProbe(spec gatewayv1alpha1.ComponentSpec) *corev1.Probe {
    if spec.ManagementPort != nil {
        return &corev1.Probe{
            ProbeHandler: corev1.ProbeHandler{
                HTTPGet: &corev1.HTTPGetAction{
                    Path: "/ready",
                    Port: intstrFromInt32(*spec.ManagementPort),
                },
            },
        }
    }
    return &corev1.Probe{
        ProbeHandler: corev1.ProbeHandler{
            TCPSocket: &corev1.TCPSocketAction{
                Port: intstrFromInt32(spec.Port),
            },
        },
    }
}

func livenessProbe(spec gatewayv1alpha1.ComponentSpec) *corev1.Probe {
    if spec.ManagementPort != nil {
        return &corev1.Probe{
            ProbeHandler: corev1.ProbeHandler{
                HTTPGet: &corev1.HTTPGetAction{
                    Path: "/health",
                    Port: intstrFromInt32(*spec.ManagementPort),
                },
            },
        }
    }
    return &corev1.Probe{
        ProbeHandler: corev1.ProbeHandler{
            TCPSocket: &corev1.TCPSocketAction{
                Port: intstrFromInt32(spec.Port),
            },
        },
    }
}

func intstrFromInt32(value int32) intstr.IntOrString {
    return intstr.FromInt32(value)
}

func raftEnv(cluster *gatewayv1alpha1.BoostGatewayCluster, component managedComponent) []corev1.EnvVar {
    replicas := replicasOrDefault(component.Spec, 1)
    serviceName := componentResourceName(cluster.Name, component.Name)
    peers := make([]string, 0, int(replicas))
    for i := int32(0); i < replicas; i++ {
        nodeName := fmt.Sprintf("%s-%d", serviceName, i)
        host := fmt.Sprintf("%s.%s.%s.svc.cluster.local",
            nodeName, serviceName, cluster.Namespace)
        peers = append(peers, fmt.Sprintf("%s@%s:%d", nodeName, host, component.Spec.Port))
    }

    return []corev1.EnvVar{
        {
            Name: "POD_NAME",
            ValueFrom: &corev1.EnvVarSource{
                FieldRef: &corev1.ObjectFieldSelector{FieldPath: "metadata.name"},
            },
        },
        {
            Name: "POD_NAMESPACE",
            ValueFrom: &corev1.EnvVarSource{
                FieldRef: &corev1.ObjectFieldSelector{FieldPath: "metadata.namespace"},
            },
        },
        {
            Name: "RAFT_NODE_ID",
            ValueFrom: &corev1.EnvVarSource{
                FieldRef: &corev1.ObjectFieldSelector{FieldPath: "metadata.name"},
            },
        },
        {Name: "RAFT_SERVICE_NAME", Value: serviceName},
        {Name: "RAFT_REPLICAS", Value: strconv.FormatInt(int64(replicas), 10)},
        {Name: "RAFT_PEERS", Value: strings.Join(peers, ",")},
        {Name: "RAFT_ELECTION_TIMEOUT_MIN_MS", Value: "150"},
        {Name: "RAFT_ELECTION_TIMEOUT_MAX_MS", Value: "300"},
        {Name: "RAFT_HEARTBEAT_INTERVAL_MS", Value: "50"},
    }
}

func summarizeDeployment(name string, deployment *appsv1.Deployment) componentRolloutStatus {
    desired := int32(0)
    if deployment.Spec.Replicas != nil {
        desired = *deployment.Spec.Replicas
    }
    status := gatewayv1alpha1.ComponentStatus{
        Name:               name,
        Kind:               "Deployment",
        DesiredReplicas:    desired,
        ReadyReplicas:      deployment.Status.ReadyReplicas,
        UpdatedReplicas:    deployment.Status.UpdatedReplicas,
        AvailableReplicas:  deployment.Status.AvailableReplicas,
        ObservedGeneration: deployment.Status.ObservedGeneration,
    }
    ready := deployment.Status.ObservedGeneration >= deployment.Generation &&
        deployment.Status.ReadyReplicas >= desired &&
        deployment.Status.UpdatedReplicas >= desired &&
        deployment.Status.AvailableReplicas >= desired
    degraded := false
    degradedReason := ""
    if deployment.Status.ObservedGeneration < deployment.Generation {
        degraded = true
        degradedReason = fmt.Sprintf("%s deployment has stale observedGeneration", name)
    } else if deployment.Status.UpdatedReplicas < desired {
        degraded = true
        degradedReason = fmt.Sprintf("%s deployment has insufficient updated replicas", name)
    } else if deployment.Status.AvailableReplicas < deployment.Status.ReadyReplicas {
        degraded = true
        degradedReason = fmt.Sprintf("%s deployment has fewer available than ready replicas", name)
    }
    return componentRolloutStatus{
        status: status, ready: ready, degraded: degraded, degradedReason: degradedReason,
    }
}

func summarizeStatefulSet(name string, statefulSet *appsv1.StatefulSet) componentRolloutStatus {
    desired := int32(0)
    if statefulSet.Spec.Replicas != nil {
        desired = *statefulSet.Spec.Replicas
    }
    status := gatewayv1alpha1.ComponentStatus{
        Name:               name,
        Kind:               "StatefulSet",
        DesiredReplicas:    desired,
        ReadyReplicas:      statefulSet.Status.ReadyReplicas,
        UpdatedReplicas:    statefulSet.Status.UpdatedReplicas,
        AvailableReplicas:  statefulSet.Status.AvailableReplicas,
        ObservedGeneration: statefulSet.Status.ObservedGeneration,
    }
    ready := statefulSet.Status.ObservedGeneration >= statefulSet.Generation &&
        statefulSet.Status.ReadyReplicas >= desired &&
        statefulSet.Status.UpdatedReplicas >= desired
    degraded := false
    degradedReason := ""
    if statefulSet.Status.ObservedGeneration < statefulSet.Generation {
        degraded = true
        degradedReason = fmt.Sprintf("%s statefulset has stale observedGeneration", name)
    } else if statefulSet.Status.UpdatedReplicas < desired {
        degraded = true
        degradedReason = fmt.Sprintf("%s statefulset has insufficient updated replicas", name)
    }
    return componentRolloutStatus{
        status: status, ready: ready, degraded: degraded, degradedReason: degradedReason,
    }
}
