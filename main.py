# -*- coding: utf-8 -*-
"""FastAPI应用主入口"""

from api import create_api_app

app = create_api_app()

if __name__ == "__main__":
    import os
    import uvicorn
    uvicorn.run(
        app,
        host=os.getenv("HOST", "0.0.0.0"),
        port=int(os.getenv("PORT", "8000")),
    )
