import { get, post, put, del, patch } from './index'

export interface Template {
  id: number
  name: string
  width_px: number
  height_px: number
  dpi: number
  min_kb: number
  max_kb: number
  allowed_bg_colors: string[]
  output_format: string
  physical_size_mm: string | null
  is_active: boolean
  remark: string | null
  created_at: string
  updated_at: string
}

export interface TemplateForm {
  name: string
  width_px: number
  height_px: number
  dpi: number
  min_kb: number
  max_kb: number
  allowed_bg_colors: string[]
  output_format: string
  physical_size_mm?: string
  remark?: string
}

export function getTemplates(): Promise<Template[]> {
  return get('/templates')
}

export function createTemplate(data: TemplateForm): Promise<Template> {
  return post('/templates', data)
}

export function updateTemplate(id: number, data: TemplateForm): Promise<Template> {
  return put(`/templates/${id}`, data)
}

export function deleteTemplate(id: number): Promise<void> {
  return del(`/templates/${id}`)
}

export function toggleTemplate(id: number, isActive: boolean): Promise<void> {
  return patch(`/templates/${id}/toggle`, { is_active: isActive })
}
