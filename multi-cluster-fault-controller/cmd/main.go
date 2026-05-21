package main

import (
	"reflect"

	corev1 "k8s.io/api/core/v1"
	"k8s.io/apimachinery/pkg/runtime"
	"k8s.io/client-go/informers"
	"k8s.io/client-go/kubernetes"
	"k8s.io/client-go/rest"
	"k8s.io/client-go/tools/clientcmd"
	ctrl "sigs.k8s.io/controller-runtime"
	"sigs.k8s.io/controller-runtime/pkg/log/zap"
	"sigs.k8s.io/controller-runtime/pkg/manager/signals"

	_ "k8s.io/client-go/plugin/pkg/client/auth"

	"github.com/ThaiNguyen54/multi-cluster-fault-controller/internal/controller"
)

var _ = runtime.NewScheme()
var _ = corev1.Pod{}

type ClusterTarget struct {
	Name       string
	Kubeconfig string
	Context    string
}

func main() {
	ctrl.SetLogger(zap.New(zap.UseDevMode(true)))
	log := ctrl.Log.WithName("main")
	ctx := signals.SetupSignalHandler()

	clusters := []ClusterTarget{
		{
			Name:       "edge1",
			Kubeconfig: "/home/ubuntu/edge1.kubeconfig",
			Context:    "edge1-admin@edge1",
		},
		{
			Name:       "edge2",
			Kubeconfig: "/home/ubuntu/edge2.kubeconfig",
			Context:    "edge2-admin@edge2",
		},
	}

	for _, cluster := range clusters {
		cluster := cluster

		config, err := buildConfigWithContext(cluster.Kubeconfig, cluster.Context)
		if err != nil {
			log.Error(err, "failed to build config",
				"cluster", cluster.Name,
				"context", cluster.Context,
				"kubeconfig", cluster.Kubeconfig,
			)
			continue
		}

		clientset, err := kubernetes.NewForConfig(config)
		if err != nil {
			log.Error(err, "failed to create clientset",
				"cluster", cluster.Name,
				"context", cluster.Context,
			)
			continue
		}

		factory := informers.NewSharedInformerFactory(clientset, 0)

		detector := &controller.FaultDetector{
			Log:         ctrl.Log.WithName("fault-detector").WithValues("cluster", cluster.Name),
			ClusterName: cluster.Name,
		}

		checker := &controller.IngressChecker{
			FaultDetector: detector,
			Client:        clientset,
		}

		podInformer := factory.Core().V1().Pods().Informer()
		nodeInformer := factory.Core().V1().Nodes().Informer()
		endpointInformer := factory.Core().V1().Endpoints().Informer()
		ingressInformer := factory.Networking().V1().Ingresses().Informer()
		serviceInformer := factory.Core().V1().Services().Informer()

		podInformer.AddEventHandler(detector.PodEventHandler())
		nodeInformer.AddEventHandler(detector.NodeEventHandler())
		endpointInformer.AddEventHandler(detector.EndpointEventHandler())
		ingressInformer.AddEventHandler(checker.IngressEventHandler())
		serviceInformer.AddEventHandler(checker.ServiceEventHandler())

		factory.Start(ctx.Done())

		synced := factory.WaitForCacheSync(ctx.Done())
		if !allSynced(synced) {
			log.Error(nil, "failed to sync informer caches",
				"cluster", cluster.Name,
				"context", cluster.Context,
			)
			continue
		}

		log.Info("cluster informers synced and watching",
			"cluster", cluster.Name,
			"context", cluster.Context,
			"kubeconfig", cluster.Kubeconfig,
		)
	}

	log.Info("all cluster informers started and synced")
	<-ctx.Done()
	log.Info("shutting down")
}

func buildConfigWithContext(kubeconfigPath, contextName string) (*rest.Config, error) {
	loadingRules := &clientcmd.ClientConfigLoadingRules{
		ExplicitPath: kubeconfigPath,
	}
	overrides := &clientcmd.ConfigOverrides{
		CurrentContext: contextName,
	}
	return clientcmd.NewNonInteractiveDeferredLoadingClientConfig(loadingRules, overrides).ClientConfig()
}

func allSynced(syncMap map[reflect.Type]bool) bool {
	for _, ok := range syncMap {
		if !ok {
			return false
		}
	}
	return true
}

// package main

// import (
// 	corev1 "k8s.io/api/core/v1"
// 	"k8s.io/apimachinery/pkg/runtime"
// 	"k8s.io/client-go/informers"
// 	"k8s.io/client-go/kubernetes"
// 	"k8s.io/client-go/tools/clientcmd"
// 	ctrl "sigs.k8s.io/controller-runtime"
// 	"sigs.k8s.io/controller-runtime/pkg/log/zap"
// 	"sigs.k8s.io/controller-runtime/pkg/manager/signals"

// 	_ "k8s.io/client-go/plugin/pkg/client/auth"

// 	"github.com/ThaiNguyen54/multi-cluster-fault-controller/internal/controller"
// )

// var _ = runtime.NewScheme()
// var _ = corev1.Pod{}

// func main() {
// 	ctrl.SetLogger(zap.New(zap.UseDevMode(true)))
// 	log := ctrl.Log.WithName("main")
// 	ctx := signals.SetupSignalHandler()

// 	clusters := map[string]string{
// 		"edge1": "/home/ubuntu/edge1.kubeconfig",
// 		"edge2": "/home/ubuntu/edge2.kubeconfig",
// 	}

// 	for clusterName, kubeconfigPath := range clusters {
// 		clusterName := clusterName
// 		kubeconfigPath := kubeconfigPath

// 		config, err := clientcmd.BuildConfigFromFlags("", kubeconfigPath)
// 		if err != nil {
// 			log.Error(err, "failed to build config", "cluster", clusterName)
// 			continue
// 		}

// 		clientset, err := kubernetes.NewForConfig(config)
// 		if err != nil {
// 			log.Error(err, "failed to create clientset", "cluster", clusterName)
// 			continue
// 		}

// 		factory := informers.NewSharedInformerFactory(clientset, 0)

// 		detector := &controller.FaultDetector{
// 			Log:         ctrl.Log.WithName("fault-detector"),
// 			ClusterName: clusterName,
// 		}

// 		checker := &controller.IngressChecker{
// 			FaultDetector: detector,
// 			Client:        clientset,
// 		}

// 		// Pod + Node watchers
// 		factory.Core().V1().Pods().Informer().AddEventHandler(detector.PodEventHandler())
// 		factory.Core().V1().Nodes().Informer().AddEventHandler(detector.NodeEventHandler())

// 		// Endpoint watcher (detects readyAddresses=0)
// 		factory.Core().V1().Endpoints().Informer().AddEventHandler(detector.EndpointEventHandler())

// 		// Ingress + Service watchers (detects targetPort mismatch → 502)
// 		factory.Networking().V1().Ingresses().Informer().AddEventHandler(checker.IngressEventHandler())
// 		factory.Core().V1().Services().Informer().AddEventHandler(checker.ServiceEventHandler())

// 		go factory.Start(ctx.Done())
// 		log.Info("started informers", "cluster", clusterName, "kubeconfig", kubeconfigPath)
// 	}

// 	log.Info("all cluster informers started — watching edge1 + edge2 for faults")
// 	<-ctx.Done()
// 	log.Info("shutting down")
// }
