workspace "Asistente Académico FISI" "Arquitectura del sistema de consultas académicas (sílabos, matrícula, reglamento) sin RAG." {

    model {
        # Actores
        alumno = person "Alumno FISI" "Estudiante de pregrado o posgrado que consulta sobre sílabos, matrícula y reglamentos." "Persona"
        docente = person "Docente FISI" "Profesor que verifica normativas académicas, sumillas y reglamentos vigentes." "Persona"
        admin = person "Administrador TI" "Gestiona el servidor, actualiza los documentos base y monitorea el servicio." "Persona"

        # Sistemas Externos
        sistemasFisi = softwareSystem "Portales Institucionales FISI / UNMSM" "Fuente oficial de documentos PDF/texto (sílabos, reglamento académico, guías de matrícula)." "Sistema Externo"

        # Sistema Principal
        asistenteFisi = softwareSystem "Asistente Académico FISI" "Proporciona respuestas precisas sobre normativa y trámites académicos usando un LLM con inyección de contexto (Sin RAG)." {
            
            # Contenedores (Nivel C2)
            webApp = container "Aplicación Web Cliente" "Interfaz de chat responsiva para estudiantes y docentes." "React / Next.js / TypeScript" "Web Browser"
            
            apiGateway = container "Backend Orquestador / API Gateway" "Gestiona sesiones, control de acceso, rate-limiting y ensambla el System Prompt con la base de conocimientos." "Python (FastAPI) / Node.js"
            
            knowledgeBase = container "Base de Conocimientos Estática" "Almacena los textos planos consolidados y curados de sílabos, reglamento y matrícula." "Archivos Markdown"
            
            inferenceEngine = container "Motor de Inferencia LLM" "Ejecuta el modelo Qwen2.5 3B (o 7B en producción) procesando el contexto completo y generando tokens en streaming." "Ollama (Local) / vLLM (Producción 20 usuarios)"
            
            modelStorage = container "Almacenamiento de Pesos del Modelo" "Almacena los tensores cuantizados del modelo LLM." "Disco NVMe (.gguf / .safetensors)" "Folder"
        }

        # Relaciones C1 (Contexto)
        alumno -> asistenteFisi "Realiza consultas académicas en lenguaje natural"
        docente -> asistenteFisi "Consulta normativas y sílabos"
        admin -> asistenteFisi "Actualiza documentos normativos y administra el despliegue"
        asistenteFisi -> sistemasFisi "Obtiene documentos oficiales para consolidación manual/periódica"

        # Relaciones C2 (Contenedores)
        alumno -> webApp "Interactúa mediante chat web" "HTTPS"
        docente -> webApp "Interactúa mediante chat web" "HTTPS"
        webApp -> apiGateway "Envía consultas y recibe respuestas en streaming" "REST / SSE (JSON)"
        
        admin -> knowledgeBase "Actualiza y cura los textos de reglamentos y sílabos" "SSH / SFTP / CI-CD"
        admin -> inferenceEngine "Configura parámetros del modelo y monitorea recursos" "CLI / API"
        sistemasFisi -> knowledgeBase "Provee documentos fuente (extracción previa)" "Manual / Script ETL"

        apiGateway -> knowledgeBase "Lee la base de conocimientos completa para inyectarla en el contexto" "Lectura en Memoria / Disk I/O"
        apiGateway -> inferenceEngine "Envía prompt enriquecido (System Prompt + KB + Pregunta)" "HTTP REST / OpenAI-Compatible API"
        inferenceEngine -> modelStorage "Carga pesos del modelo en VRAM/RAM al iniciar" "NVMe I/O"
    }

    views {
        # Vista C1: Contexto del Sistema
        systemContext asistenteFisi "C1_Contexto" "Diagrama de Contexto del Sistema (C1)" {
            include *
            autoLayout tb
        }

        # Vista C2: Contenedores
        container asistenteFisi "C2_Contenedores" "Diagrama de Contenedores (C2)" {
            include *
            autoLayout tb
        }

        styles {
            element "Element" {
              background "#ffffff"
              strokeWidth 7
            }
            element "Software System" {
                stroke "#198754"
                color "#198754"
                shape RoundedBox
            }
            element "Sistema Externo" {
                shape RoundedBox
                stroke "#6f42c1"
                color "#6f42c1"
            }
            element "Persona" {
                shape Person
                stroke "#0d6efd"
                color "#0d6efd"
            }
            element "Container" {
                shape Box
                stroke "#0dcaf0"
                color "#0dcaf0"
            }
            element "Web Browser" {
                shape WebBrowser
                stroke "#fd7e14"
                color "#fd7e14"
            }
            element "Database" {
                shape Cylinder
                stroke "#ffc107"
                color "#ffc107"
            }
            element "Folder" {
                shape Folder
                stroke "#ffc107"
                color "#ffc107"
            }
        }
    }
}
