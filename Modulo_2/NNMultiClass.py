import numpy as np

def one_hot(y, n_classes):
    Y = np.zeros((y.shape[0], n_classes))
    Y[np.arange(y.shape[0]), y] = 1.0
    return Y


class NNMultiClass:
    def __init__(self, layer_sizes, lr=1e-2, seed=42, hidden_activation="relu"):
        rng = np.random.default_rng(seed)
        self.layer_sizes = layer_sizes
        self.L = len(layer_sizes) - 1
        self.lr = lr
        self.hidden_activation = hidden_activation

        self.weights = {}
        self.biases = {}
        for l in range(self.L):
            n_in, n_out = layer_sizes[l], layer_sizes[l+1]
            if l < self.L - 1:  # capa oculta
                if hidden_activation.lower() == "relu":
                    # He
                    std = np.sqrt(2.0 / n_in)
                else:
                    std = np.sqrt(1.0 / n_in)
            else:
                std = np.sqrt(1.0 / n_in)

            self.weights[l] = rng.normal(0.0, std, size=(n_in, n_out))
            self.biases[l]  = np.zeros((1, n_out))

    @staticmethod
    def _relu(Z): return np.maximum(0, Z)
    @staticmethod
    def _drelu(Z): return (Z > 0).astype(Z.dtype)

    @staticmethod
    def _softmax(Z):
        Zs = Z - Z.max(axis=1, keepdims=True)
        e = np.exp(Zs)
        return e / e.sum(axis=1, keepdims=True)

    def _forward(self, X):
        A = X
        caches = {"A0": X}
        for l in range(self.L):
            W, b = self.weights[l], self.biases[l]
            Z = A @ W + b
            caches[f"Z{l+1}"] = Z
            if l < self.L - 1:
                if self.hidden_activation == "relu":
                    A = self._relu(Z)
                else:
                    A = np.tanh(Z)
            else:
                A = self._softmax(Z)  
            caches[f"A{l+1}"] = A
        return A, caches

    # ---- Backward ----
    def _backward(self, caches, Y_onehot):
        grads = {}
        # dA en la salida con CE + Softmax: dZ = A - Y POR LO TANTO SE NECESITAN 2 NEURONAS DE SALIDA ! 
        A_L = caches[f"A{self.L}"]
        dZ = (A_L - Y_onehot) 

        for l in reversed(range(self.L)):
            A_prev = caches[f"A{l}"]
            W = self.weights[l]
            dW = A_prev.T @ dZ / A_prev.shape[0]
            db = dZ.mean(axis=0, keepdims=True)
            grads[f"dW{l}"] = dW
            grads[f"db{l}"] = db

            if l > 0:
                Z_prev = caches[f"Z{l}"]
                dA_prev = dZ @ W.T
                if self.hidden_activation == "relu":
                    dZ = dA_prev * self._drelu(Z_prev)
                else:
                    dZ = dA_prev * (1.0 - np.tanh(Z_prev) ** 2)
        return grads

    def _step(self, grads):
        for l in range(self.L):
            self.weights[l] -= self.lr * grads[f"dW{l}"]
            self.biases[l]  -= self.lr * grads[f"db{l}"]
    @staticmethod
    def _cross_entropy(probs, Y_onehot, eps=1e-12):
        p = np.clip(probs, eps, 1 - eps)
        return -np.mean(np.sum(Y_onehot * np.log(p), axis=1))

    def fit(self, X, y, epochs=200, batch_size=64, verbose=True):
        n, n_classes = X.shape[0], self.layer_sizes[-1]
        Y = one_hot(y.astype(int), n_classes)

        idx = np.arange(n)
        for ep in range(1, epochs + 1):
            np.random.shuffle(idx)
            Xs, Ys = X[idx], Y[idx]

            # Minibatches
            for i in range(0, n, batch_size):
                xb = Xs[i:i+batch_size]
                yb = Ys[i:i+batch_size]

                probs, caches = self._forward(xb)
                grads = self._backward(caches, yb)
                self._step(grads)

            # Métrica por época
            probs_full, _ = self._forward(X)
            loss = self._cross_entropy(probs_full, Y)
            if verbose and (ep % max(1, epochs // 10) == 0 or ep == 1):
                acc = (probs_full.argmax(axis=1) == y).mean()
                print(f"Epoch {ep:4d} | loss={loss:.4f} | acc={acc:.4f}")

    def predict_proba(self, X):
        probs, _ = self._forward(X)
        return probs

    def predict(self, X):
        return self.predict_proba(X).argmax(axis=1)

    def show_weights(self):
        for i, W in self.weights.items():
            print(f"\nPesos capa {i} ({W.shape[0]} → {W.shape[1]}):")
            print(W)
    def set_weights(self, weights, biases=None, strict=True):
        import numpy as np

        def _is_seq(x):
            return isinstance(x, (list, tuple))

        def _as_dict(obj, name):
            if isinstance(obj, dict):
                return obj
            if _is_seq(obj):
                if len(obj) != self.L:
                    raise ValueError(f"{name} debe tener longitud {self.L}; recibí {len(obj)}.")
                return {i: obj[i] for i in range(self.L)}
            raise ValueError(f"{name} debe ser list/tuple o dict.")

        def _coerce_bias(b, out_dim):
            b = np.asarray(b)
            if b.ndim == 1:
                if b.shape[0] != out_dim:
                    raise ValueError(f"Bias dim inválida: esperado ({out_dim},) o (1,{out_dim}), recibí {b.shape}.")
                return b.reshape(1, -1)
            if b.ndim == 2:
                if b.shape != (1, out_dim):
                    if strict:
                        raise ValueError(f"Bias forma inválida: esperado (1,{out_dim}), recibí {b.shape}.")
                    if b.shape == (out_dim, 1):
                        return b.T
                    raise ValueError(f"Bias forma incompatible: {b.shape}.")
                return b
            raise ValueError(f"Bias debe ser vector o matriz fila, recibí ndim={b.ndim}.")

        Wd = _as_dict(weights, "weights")

        Bd = None
        if biases is not None:
            Bd = _as_dict(biases, "biases")

        for l, W_new in Wd.items():
            if l not in self.weights:
                raise ValueError(f"Capa {l} no existe (0..{self.L-1}).")
            W_new = np.asarray(W_new)
            expected_shape = self.weights[l].shape
            if W_new.shape != expected_shape:
                raise ValueError(
                    f"weights[{l}] forma {W_new.shape} != esperado {expected_shape}."
                )
            self.weights[l] = W_new

            if Bd is not None and l in Bd:
                out_dim = expected_shape[1]
                self.biases[l] = _coerce_bias(Bd[l], out_dim)

        if Bd is not None:
            for l, b_new in Bd.items():
                if l not in self.biases:
                    raise ValueError(f"Capa {l} no existe para bias (0..{self.L-1}).")
                if l in Wd:
                    continue
                out_dim = self.weights[l].shape[1]
                self.biases[l] = _coerce_bias(b_new, out_dim)