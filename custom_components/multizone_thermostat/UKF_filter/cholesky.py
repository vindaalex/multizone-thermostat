from math import sqrt
import numpy as np


def cholesky(A, upper=True):
    """Performs a Cholesky decomposition of A, which must 
    be a symmetric and positive definite matrix."""
    
    if upper:
        """
        https://github.com/TayssirDo/Cholesky-decomposition/blob/master/cholesky.py
        Compute the cholesky decomposition of a SPD matrix M.
        :param M: (N, N) real valued matrix.
        :return: R: (N, N) upper triangular matrix with positive diagonal entries if M is SPD.
        """
        M = np.copy(A)
        n = A.shape[0]
        R = np.zeros_like(M)

        for k in range(n):
            R[k, k] = sqrt(M[k, k])
            R[k, k + 1:] = M[k, k + 1:] / R[k, k]
            for j in range(k + 1, n):
                M[j, j:] = M[j, j:] - R[k, j] * R[k, j:]

        return R
    else:
        """
        https://www.quantstart.com/articles/Cholesky-Decomposition-in-Python-and-NumPy/
        This function returns the lower variant triangular matrix,
        """
        n = len(A)

        # Create zero matrix for L
        L = [[0.0] * n for i in range(n)]

        # Perform the Cholesky decomposition
        for i in range(n):
            for k in range(i+1):
                tmp_sum = sum(L[i][j] * L[k][j] for j in range(k))
                
                if (i == k): # Diagonal elements
                    # LaTeX: l_{kk} = \sqrt{ a_{kk} - \sum^{k-1}_{j=1} l^2_{kj}}
                    L[i][k] = sqrt(A[i][i] - tmp_sum)
                else:
                    # LaTeX: l_{ik} = \frac{1}{l_{kk}} \left( a_{ik} - \sum^{k-1}_{j=1} l_{ij} l_{kj} \right)
                    L[i][k] = (1.0 / L[k][k] * (A[i][k] - tmp_sum))
        return L

