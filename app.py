import urllib2, math, json, glob, rdflib
from flask import Flask, abort, redirect, request, make_response
from flask_negotiation import provides
from rdflib import Graph, URIRef, BNode, Literal
from rdflib.namespace import RDF, VOID, XSD
from rdflib.plugin import register, Serializer
from itertools import izip_longest
from uritemplate import URITemplate, expand

app = Flask(__name__)

rdflib.plugin.register('application/ld+json', Serializer, 'rdflib_jsonld.serializer', 'JsonLDSerializer')
rdflib.plugin.register('json-ld', Serializer, 'rdflib_jsonld.serializer', 'JsonLDSerializer')

CONFIG = json.load(open('config.json', 'r'))
DATASET_RELATIVE_URI = URITemplate("{/context,subcontext}/").expand(context=CONFIG['context'], subcontext=CONFIG['subcontext'])
WELL_KNOWN_VOID_RELATIVE_URI = URITemplate("{/context,subcontext}/.well-known/void").expand(context=CONFIG['context'], subcontext=CONFIG['subcontext'])
RESOURCE_RELATIVE_URI = URITemplate("{/context,subcontext}/<path:resource>").expand(context=CONFIG['context'], subcontext=CONFIG['subcontext'])
TPF_RELATIVE_URI = URITemplate("{/context,subcontext}/fragment{?s,p,o,page}").expand(context=CONFIG['context'], subcontext=CONFIG['subcontext'])
HYDRA = rdflib.Namespace("http://www.w3.org/ns/hydra/core#")
VOID_GRAPH = Graph().parse('void.ttl', format="n3")
DATASET_GRAPH = Graph()

for entitytype in CONFIG['entitytypes']:
    for ld_doc in glob.glob('.{}{}/*.ttl'.format(DATASET_RELATIVE_URI, entitytype)):
        DATASET_GRAPH.parse(ld_doc, format="n3")

# From Python 2.x version of itertools documentation
def grouper(iterable, n, fillvalue=None):
    "Collect data into fixed-length chunks or blocks"
    # grouper('ABCDEFG', 3, 'x') --> ABC DEF Gxx
    args = [iter(iterable)] * n
    return izip_longest(fillvalue=fillvalue, *args)

def accepted_graph_serialization(graph, media_type):
    if media_type == 'application/rdf+xml':
        response = make_response(graph.serialize(None, format='xml'))
        response.headers['content-type'] = 'application/rdf+xml'
    elif media_type == 'text/plain':
        response = make_response(graph.serialize(None, format='ntriples'))
        response.headers['content-type'] = 'text/plain'
    elif media_type == 'application/ld+json':
        response = make_response(graph.serialize(None, format='json-ld', indent=4))
        response.headers['content-type'] = 'application/ld+json'
    else: # return text/turtle
        response = make_response(graph.serialize(None, format='turtle'))
        response.headers['content-type'] = 'text/turtle'
    return response

def tpf_params():
    s = request.args.get('s')
    if s:
        s = URIRef(s)
    p = request.args.get('p')
    if p:
        p = URIRef(p)
    o = request.args.get('o')
    if o:
        if o.startswith('http'):
            o = URIRef(o)
        else:
            o = Literal(o)
    triple_pattern = ( s, p, o )
    page = request.args.get('page')
    if page:
        page = int(page)
    else:
        page = 0
    return s, p, o, page

def tpf_data(s, p, o, page):
    data = Graph()
    triples = [ triple for triple in DATASET_GRAPH.triples( (s, p, o) ) ]
    n_triples = len(triples)
    for i, page_triples in enumerate(grouper(triples, CONFIG['pagesize'])):
        if i == page:
            for triple in page_triples:
                if triple:
                    data.add(triple)
            break
    return data, n_triples

def tpf_metadata(n_triples):
    metadata = Graph()
    metadata.add( (URIRef(request.url), VOID.triples, Literal(n_triples, datatype=XSD.integer)) )
    return metadata

def tpf_controls(data, n_triples, page):
    controls = Graph()
    page_size = CONFIG['pagesize']
    max_page_no = math.ceil(n_triples / page_size)
    controls.add( (URIRef(request.url_root), VOID.subset, URIRef(request.url)) )
    controls.add( (URIRef(request.url_root), HYDRA.template, Literal(TPF_RELATIVE_URI+"{?s,p,o}")) )
    s_var = BNode()
    controls.add( (s_var, HYDRA.variable, Literal("s")) )
    controls.add( (s_var, HYDRA.property, RDF.subject) )
    p_var = BNode()
    controls.add( (p_var, HYDRA.variable, Literal("p")) )
    controls.add( (p_var, HYDRA.property, RDF.predicate) )
    o_var = BNode()
    controls.add( (o_var, HYDRA.variable, Literal("o")) )
    controls.add( (o_var, HYDRA.property, RDF.object) )
    controls.add( (URIRef(request.url_root), HYDRA.mapping, s_var) )
    controls.add( (URIRef(request.url_root), HYDRA.mapping, p_var) )
    controls.add( (URIRef(request.url_root), HYDRA.mapping, o_var) )
    controls.add( (URIRef(request.url), RDF.type, HYDRA.Collection) )
    controls.add( (URIRef(request.url), RDF.type, HYDRA.PagedCollection) )
    controls.add( (URIRef(request.url), HYDRA.totalItems, Literal(n_triples, datatype=XSD.integer)) )
    controls.add( (URIRef(request.url), HYDRA.itemsPerPage, Literal(page_size, datatype=XSD.integer)) )
    if page >= 1:
        controls.add( (URIRef(request.url), HYDRA.firstPage, Literal(0, datatype=XSD.integer)) )
    if page > 1:
        controls.add( (URIRef(request.url), HYDRA.previousPage, Literal(page - 1, datatype=XSD.integer)) )
    if page < max_page_no:
        controls.add( (URIRef(request.url), HYDRA.nextPage, Literal(page + 1, datatype=XSD.integer)) )
    return controls

@app.route(DATASET_RELATIVE_URI)
@provides('text/html', 'text/turtle', 'application/rdf+xml', 'text/plain', 'application/x-turtle', 'text/rdf+n3', 'application/ld+json', to='media_type')
def get_root(media_type):
    return accepted_graph_serialization(VOID_GRAPH, media_type)

@app.route(WELL_KNOWN_VOID_RELATIVE_URI)
@provides('text/html', 'text/turtle', 'application/rdf+xml', 'text/plain', 'application/x-turtle', 'text/rdf+n3', 'application/ld+json', to='media_type')
def get_well_known_void(media_type):
    return accepted_graph_serialization(VOID_GRAPH, media_type)

@app.route(RESOURCE_RELATIVE_URI)
@provides('text/html', 'text/turtle', 'application/rdf+xml', 'text/plain', 'application/x-turtle', 'text/rdf+n3', 'application/ld+json', to='media_type')
def get_resource(resource, media_type):
    try:
        graph = Graph().parse('.{}{}.ttl'.format(request.script_root, request.path), format='n3')
    except IOError as e:
        abort(404)
    return accepted_graph_serialization(graph, media_type)

@app.route(TPF_RELATIVE_URI)
@provides('text/html', 'text/turtle', 'application/rdf+xml', 'text/plain', 'application/x-turtle', 'text/rdf+n3', 'application/ld+json', to='media_type')
def get_tpf(media_type):
    (s, p, o, page) = tpf_params()
    (data, n_triples) = tpf_data(s, p, o, page)
    metadata = tpf_metadata(n_triples)
    controls = tpf_controls(data, n_triples, page)
    graph = data + metadata + controls
    return accepted_graph_serialization(graph, media_type)
