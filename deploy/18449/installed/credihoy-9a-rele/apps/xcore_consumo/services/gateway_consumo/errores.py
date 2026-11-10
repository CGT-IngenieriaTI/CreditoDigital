class EvaluacionGatewayBloqueada(ValueError):
    def __init__(self, codigo):
        self.codigo = codigo
        super().__init__(codigo)
