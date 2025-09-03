# ========================================================================================
#                                     Imports
# ========================================================================================

import numpy as np

from matplotlib import pyplot as plt

from sklearn.metrics import ConfusionMatrixDisplay, classification_report
from sklearn.datasets import make_classification

from NNMultiClass import NNMultiClass

# ========================================================================================
#                                   Data Generation
# ========================================================================================

data = make_classification(
    n_classes=2,
    n_features=4,
    n_samples=1000,
    random_state=42
)

X = data[0]
y = data[1]

# ========================================================================================
#                                  Train Test Split
# ========================================================================================

test_percentage = 0.2

X_train = X[:-int(test_percentage * X.shape[0])]
y_train = y[:-int(test_percentage * y.shape[0])]

X_test = X[-int(test_percentage * X.shape[0]):]
y_test = y[-int(test_percentage * y.shape[0]):]

# ========================================================================================
#                              Neural Network Configuration
# ========================================================================================

input_size = X_train.shape[1]
output_size = len(np.unique(y_train))
layers = 8

layer_sizes = [input_size] + [layers] + [output_size]

nn = NNMultiClass(layer_sizes=layer_sizes, hidden_activation="a", seed=42, lr=3e-1)
nn.show_weights()
y_pred = nn.predict(X_test)

# ========================================================================================
#                                Confusion Matrix
# ========================================================================================

ConfusionMatrixDisplay.from_predictions(y_test, y_pred)
plt.title("Confusion Matrix")
plt.show()

# ========================================================================================
#                              Classification Report
# ========================================================================================

print("\n\n", classification_report(y_test, y_pred))

# ========================================================================================
#                                     End
# ========================================================================================
