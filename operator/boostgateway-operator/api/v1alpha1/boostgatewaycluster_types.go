package v1alpha1

import (
    corev1 "k8s.io/api/core/v1"
    metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
    "k8s.io/apimachinery/pkg/runtime"
)

type ComponentSpec struct {
    Enabled        *bool                           `json:"enabled,omitempty"`
    Replicas       *int32                          `json:"replicas,omitempty"`
    Image          string                          `json:"image,omitempty"`
    Port           int32                           `json:"port,omitempty"`
    ManagementPort *int32                          `json:"managementPort,omitempty"`
    Resources      corev1.ResourceRequirements     `json:"resources,omitempty"`
}

type TLSConfig struct {
    Enabled          bool   `json:"enabled,omitempty"`
    SecretName       string `json:"secretName,omitempty"`
    CertManagerIssuer string `json:"certManagerIssuer,omitempty"`
    ManagedByCertManager bool `json:"managedByCertManager,omitempty"`
}

type BoostGatewayClusterSpec struct {
    ImageRegistry string        `json:"imageRegistry,omitempty"`
    ImageTag      string        `json:"imageTag,omitempty"`
    PullPolicy    corev1.PullPolicy `json:"pullPolicy,omitempty"`
    Gateway       ComponentSpec `json:"gateway,omitempty"`
    Login         ComponentSpec `json:"login,omitempty"`
    Room          ComponentSpec `json:"room,omitempty"`
    Battle        ComponentSpec `json:"battle,omitempty"`
    Match         ComponentSpec `json:"match,omitempty"`
    Leaderboard   ComponentSpec `json:"leaderboard,omitempty"`
    TLS           TLSConfig     `json:"tls,omitempty"`
}

type BoostGatewayClusterStatus struct {
    Phase         string             `json:"phase,omitempty"`
    ReadyReplicas int32              `json:"readyReplicas,omitempty"`
    DesiredReplicas int32            `json:"desiredReplicas,omitempty"`
    Components    []ComponentStatus  `json:"components,omitempty"`
    Conditions    []metav1.Condition `json:"conditions,omitempty"`
}

type ComponentStatus struct {
    Name             string `json:"name,omitempty"`
    Kind             string `json:"kind,omitempty"`
    DesiredReplicas  int32  `json:"desiredReplicas,omitempty"`
    ReadyReplicas    int32  `json:"readyReplicas,omitempty"`
    UpdatedReplicas  int32  `json:"updatedReplicas,omitempty"`
    AvailableReplicas int32 `json:"availableReplicas,omitempty"`
    ObservedGeneration int64 `json:"observedGeneration,omitempty"`
}

type BoostGatewayCluster struct {
    metav1.TypeMeta   `json:",inline"`
    metav1.ObjectMeta `json:"metadata,omitempty"`

    Spec   BoostGatewayClusterSpec   `json:"spec,omitempty"`
    Status BoostGatewayClusterStatus `json:"status,omitempty"`
}

type BoostGatewayClusterList struct {
    metav1.TypeMeta `json:",inline"`
    metav1.ListMeta `json:"metadata,omitempty"`
    Items           []BoostGatewayCluster `json:"items"`
}

func (in *ComponentSpec) DeepCopyInto(out *ComponentSpec) {
    *out = *in
    if in.Enabled != nil {
        out.Enabled = new(bool)
        *out.Enabled = *in.Enabled
    }
    if in.Replicas != nil {
        out.Replicas = new(int32)
        *out.Replicas = *in.Replicas
    }
    if in.ManagementPort != nil {
        out.ManagementPort = new(int32)
        *out.ManagementPort = *in.ManagementPort
    }
    in.Resources.DeepCopyInto(&out.Resources)
}

func (in *BoostGatewayClusterSpec) DeepCopyInto(out *BoostGatewayClusterSpec) {
    *out = *in
    in.Gateway.DeepCopyInto(&out.Gateway)
    in.Login.DeepCopyInto(&out.Login)
    in.Room.DeepCopyInto(&out.Room)
    in.Battle.DeepCopyInto(&out.Battle)
    in.Match.DeepCopyInto(&out.Match)
    in.Leaderboard.DeepCopyInto(&out.Leaderboard)
}

func (in *BoostGatewayClusterStatus) DeepCopyInto(out *BoostGatewayClusterStatus) {
    *out = *in
    if in.Components != nil {
        out.Components = make([]ComponentStatus, len(in.Components))
        copy(out.Components, in.Components)
    }
    if in.Conditions != nil {
        out.Conditions = make([]metav1.Condition, len(in.Conditions))
        for i := range in.Conditions {
            in.Conditions[i].DeepCopyInto(&out.Conditions[i])
        }
    }
}

func (in *BoostGatewayCluster) DeepCopyInto(out *BoostGatewayCluster) {
    *out = *in
    out.TypeMeta = in.TypeMeta
    in.ObjectMeta.DeepCopyInto(&out.ObjectMeta)
    in.Spec.DeepCopyInto(&out.Spec)
    in.Status.DeepCopyInto(&out.Status)
}

func (in *BoostGatewayCluster) DeepCopyObject() runtime.Object {
    if in == nil {
        return nil
    }
    out := new(BoostGatewayCluster)
    in.DeepCopyInto(out)
    return out
}

func (in *BoostGatewayClusterList) DeepCopyInto(out *BoostGatewayClusterList) {
    *out = *in
    out.TypeMeta = in.TypeMeta
    in.ListMeta.DeepCopyInto(&out.ListMeta)
    if in.Items != nil {
        out.Items = make([]BoostGatewayCluster, len(in.Items))
        for i := range in.Items {
            in.Items[i].DeepCopyInto(&out.Items[i])
        }
    }
}

func (in *BoostGatewayClusterList) DeepCopyObject() runtime.Object {
    if in == nil {
        return nil
    }
    out := new(BoostGatewayClusterList)
    in.DeepCopyInto(out)
    return out
}
