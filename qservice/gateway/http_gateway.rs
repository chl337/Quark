// Copyright (c) 2021 Quark Container Authors / 2018 The gVisor Authors.
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under

use axum::extract::{Request, State};
use axum::response::Response;
use axum::{
    body::Body, extract::Path, response::IntoResponse, routing::delete, routing::get,
    routing::post, Json, Router,
};
use hyper::{StatusCode, Uri};
use hyper_util::client::legacy::connect::HttpConnector;
use hyper_util::rt::TokioExecutor;
use serde::{Deserialize, Serialize};
use std::result::Result as SResult;

//use hyper_util::{client::legacy::connect::HttpConnector, rt::TokioExecutor};

type Client = hyper_util::client::legacy::Client<HttpConnector, Body>;

use qshare::common::*;
use qshare::obj_mgr::func_mgr::*;
use qshare::obj_mgr::namespace_mgr::NamespaceSpec;

use crate::func_agent_mgr::FUNCAGENT_MGR;
use crate::NAMESPACE_STORE;
use crate::OBJ_REPO;

pub struct HttpGateway {}

impl HttpGateway {
    pub async fn HttpServe(&self) -> Result<()> {
        let client: Client =
            hyper_util::client::legacy::Client::<(), ()>::builder(TokioExecutor::new())
                .build(HttpConnector::new());

        let app = Router::new()
            .route("/namespaces/", post(PostNamespace))
            .route("/funcpackages/", post(PostFuncPackage))
            .route(
                "/funcpackages/:tenant/:namespace/:name",
                delete(DropFuncPackage),
            )
            .route(
                "/funcpackages/:tenant/:namespace/:name",
                get(GetFuncPackage),
            )
            .route("/funcpackages/:tenant/:namespace", get(GetFuncPackages))
            .route("/funcpods/:tenant/:namespace/:name", get(GetFuncPods))
            .route("/funccall/*rest", post(PostCall))
            .with_state(client);

        let listener = tokio::net::TcpListener::bind("127.0.0.1:4000")
            .await
            .unwrap();
        println!("listening on {}", listener.local_addr().unwrap());
        axum::serve(listener, app).await.unwrap();

        return Ok(());
    }
}

async fn PostNamespace(Json(spec): Json<NamespaceSpec>) -> impl IntoResponse {
    if OBJ_REPO
        .get()
        .unwrap()
        .ContainsNamespace(&spec.tenant, &spec.namespace)
    {
        match NAMESPACE_STORE.get().unwrap().UpdateNamespace(&spec).await {
            Err(e) => (StatusCode::BAD_REQUEST, Json(format!("{:?}", e))),
            Ok(()) => (StatusCode::OK, Json(format!("ok"))),
        }
    } else {
        match NAMESPACE_STORE.get().unwrap().CreateNamespace(&spec).await {
            Err(e) => (StatusCode::BAD_REQUEST, Json(format!("{:?}", e))),
            Ok(()) => (StatusCode::OK, Json(format!("ok"))),
        }
    }
}

async fn GetFuncPods(
    Path((tenant, namespace, funcName)): Path<(String, String, String)>,
) -> impl IntoResponse {
    match OBJ_REPO
        .get()
        .unwrap()
        .GetFuncPods(&tenant, &namespace, &funcName)
    {
        Err(e) => (StatusCode::BAD_REQUEST, Json(format!("{:?}", e))),
        Ok(pods) => {
            let pods = serde_json::to_string_pretty(&pods).unwrap();
            (StatusCode::OK, Json(pods))
        }
    }
}

async fn PostFuncPackage(Json(spec): Json<FuncPackageSpec>) -> impl IntoResponse {
    match OBJ_REPO
        .get()
        .unwrap()
        .ContainsFuncPackage(&spec.tenant, &spec.namespace, &spec.funcname)
    {
        Err(e) => (StatusCode::BAD_REQUEST, Json(format!("{:?}", e))),
        Ok(contains) => {
            if contains {
                match NAMESPACE_STORE
                    .get()
                    .unwrap()
                    .UpdateFuncPackage(&spec)
                    .await
                {
                    Err(e) => (StatusCode::BAD_REQUEST, Json(format!("{:?}", e))),
                    Ok(()) => (StatusCode::OK, Json(format!("ok"))),
                }
            } else {
                match NAMESPACE_STORE
                    .get()
                    .unwrap()
                    .CreateFuncPackage(&spec)
                    .await
                {
                    Err(e) => (StatusCode::BAD_REQUEST, Json(format!("{:?}", e))),
                    Ok(()) => (StatusCode::OK, Json(format!("ok"))),
                }
            }
        }
    }
}

async fn DropFuncPackage(
    Path((tenant, namespace, name)): Path<(String, String, String)>,
) -> impl IntoResponse {
    match OBJ_REPO
        .get()
        .unwrap()
        .GetFuncPackage(&tenant, &namespace, &name)
    {
        Err(e) => (StatusCode::BAD_REQUEST, Json(format!("{:?}", e))),
        Ok(funcPackage) => {
            let revision = funcPackage.spec.revision;
            match NAMESPACE_STORE
                .get()
                .unwrap()
                .DropFuncPackage(&tenant, &namespace, &name, revision)
                .await
            {
                Err(e) => (StatusCode::BAD_REQUEST, Json(format!("{:?}", e))),
                Ok(()) => (StatusCode::OK, Json(format!("ok"))),
            }
        }
    }
}

async fn GetFuncPackage(
    Path((tenant, namespace, name)): Path<(String, String, String)>,
) -> impl IntoResponse {
    match OBJ_REPO
        .get()
        .unwrap()
        .GetFuncPackage(&tenant, &namespace, &name)
    {
        Err(e) => (StatusCode::BAD_REQUEST, Json(format!("{:?}", e))),
        Ok(funcPackage) => {
            let spec = funcPackage.spec.ToJson();
            (StatusCode::OK, Json(spec))
        }
    }
}

async fn GetFuncPackages(Path((tenant, namespace)): Path<(String, String)>) -> impl IntoResponse {
    match OBJ_REPO.get().unwrap().GetFuncPackages(&tenant, &namespace) {
        Err(e) => (StatusCode::BAD_REQUEST, Json(format!("{:?}", e))),
        Ok(funcPackages) => {
            let str = serde_json::to_string(&funcPackages).unwrap(); // format!("{:#?}", funcPackages);
            (StatusCode::OK, Json(str))
        }
    }
}

async fn PostCall(
    State(_client): State<Client>,
    mut req: Request,
) -> SResult<Response, StatusCode> {
    let path = req.uri().path();

    let parts = path.split("/").collect::<Vec<&str>>();

    let mut client = match FUNCAGENT_MGR
        .GetClient(&parts[2], &parts[3], &parts[4])
        .await
    {
        Err(e) => {
            return Ok(Response::new(Body::from(format!("error1 is {:?}", e))));
        }
        Ok(client) => client,
    };

    let uri = format!("http://127.0.0.1/funccall");
    *req.uri_mut() = Uri::try_from(uri).unwrap();

    let mut res = client.Send(req).await.unwrap();

    use http_body_util::BodyExt;
    let bytes = res
        .frame()
        .await
        //.map(|frame| frame.data_ref().unwrap())
        .map(|f| f.unwrap().into_data().unwrap())
        .unwrap();

    // we have to get whole body instead of streaming the output to client
    // todo: fix this.

    // res = hyper::Response<hyper::body::Incoming>
    // axum::body::Body

    // error!("PostCall 3");
    // let mut output = String::new();

    // while let Some(next) = res.frame().await {
    //     match next {
    //         Err(e) => return Ok(Response::new(Body::from(format!("error2 is {:?}", e)))),
    //         Ok(frame) => {
    //             let chunk = frame.data_ref().unwrap().to_vec();
    //             let str = String::from_utf8(chunk).unwrap();
    //             output = output + &str;
    //         }
    //     }
    // }

    // error!("PostCall 4 {}", &output);
    // return Ok(Response::new(Body::from(output)));

    let body: Body = Body::from(bytes);
    return Ok(Response::new(body));
}

#[derive(Serialize, Deserialize, Debug, Default)]
pub struct PromptReq {
    pub tenant: String,
    pub namespace: String,
    pub funcname: String,
    pub prompt: String,
}
