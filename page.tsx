"use client"

import React, { useState, useEffect } from 'react'
import { useParams, useRouter } from 'next/navigation'
import Link from 'next/link'
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Textarea } from "@/components/ui/textarea"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { useToast } from "@/components/ui/use-toast"
import { AlertCircle } from 'lucide-react'

interface ArticleData {
  id: number;
  title: string;
  entities: string;
  article: string;
  image_url: string;
  meta_title: string;
}

export default function EditArticle() {
  const [articleData, setArticleData] = useState<ArticleData | null>(null)
  const [imageError, setImageError] = useState(false)
  const { toast } = useToast()
  const params = useParams()
  const router = useRouter()
  const articleId = params.id

  useEffect(() => {
    const fetchArticle = async () => {
      try {
        const response = await fetch(`http://127.0.0.1:5000/fetch-single-article?id=${articleId}`)
        if (!response.ok) {
          throw new Error('Failed to fetch article')
        }
        const data = await response.json()
        setArticleData(data)
      } catch (error) {
        console.error('Error fetching article:', error)
        toast({
          title: "Error",
          description: "Failed to fetch article",
          variant: "destructive",
        })
      }
    }

    if (articleId) {
      fetchArticle()
    }
  }, [articleId, toast])

  const handleSaveChanges = async () => {
    if (!articleData) return;

    try {
      const username = localStorage.getItem('username') || 'guest';
      const response = await fetch(`http://127.0.0.1:5000/update-article?id=${articleData.id}`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          username,
          new_article: articleData.article,
          new_title: articleData.title,
          new_entities: articleData.entities,
        }),
      });

      if (!response.ok) {
        throw new Error('Failed to update article');
      }

      toast({
        title: "Success",
        description: "Article updated successfully",
      });

      // Redirect to generation history page after successful update
      router.push('/generation-history');
    } catch (error) {
      console.error('Error updating article:', error);
      toast({
        title: "Error",
        description: "Failed to update article",
        variant: "destructive",
      });
    }
  };

  if (!articleData) {
    return <div>Loading...</div>
  }

  return (
    <div className="min-h-screen bg-gray-100 p-8">
      <Card className="max-w-4xl mx-auto">
        <CardHeader>
          <CardTitle className="text-2xl font-bold bg-gradient-to-r from-gray-900 via-blue-800 to-purple-900 bg-clip-text text-transparent">Edit Article</CardTitle>
        </CardHeader>
        <CardContent className="space-y-6">
          <div>
            <label htmlFor="title" className="block text-sm font-medium text-gray-700 mb-1">
              Title
            </label>
            <Input
              id="title"
              value={articleData.title}
              onChange={(e) => setArticleData({ ...articleData, title: e.target.value })}
              className="w-full"
            />
          </div>

          <div>
            <label htmlFor="entities" className="block text-sm font-medium text-gray-700 mb-1">
              Entities
            </label>
            <Input
              id="entities"
              value={articleData.entities}
              onChange={(e) => setArticleData({ ...articleData, entities: e.target.value })}
              className="w-full"
            />
          </div>

          <div>
            <label htmlFor="content" className="block text-sm font-medium text-gray-700 mb-1">
              Content
            </label>
            <Textarea
              id="content"
              value={articleData.article}
              onChange={(e) => setArticleData({ ...articleData, article: e.target.value })}
              className="w-full h-64"
            />
          </div>

          {articleData.image_url && (
            <div>
              <h3 className="text-lg font-medium text-gray-900 mb-2">Image</h3>
              <div className="relative w-full h-64 bg-gray-200 rounded-lg overflow-hidden">
              {!imageError ? (
                  <img 
                    src={`http://127.0.0.1:5000/images/${articleData.image_url}`}
                    alt="Article image" 
                    className="object-cover w-full h-full"
                    onError={() => setImageError(true)}
                  />
                ) : (
                  <div className="flex flex-col items-center justify-center w-full h-full bg-gray-100">
                    <AlertCircle className="w-12 h-12 text-gray-400 mb-2" />
                    <p className="text-sm text-gray-500">Image failed to load</p>
                    <Button 
                      variant="outline" 
                      size="sm" 
                      className="mt-2"
                      onClick={() => setImageError(false)}
                    >
                      Retry
                    </Button>
                  </div>
                )}
              </div>
              <p className="text-sm text-gray-500 mt-2">{articleData.meta_title}</p>
            </div>
          )}

          <div className="flex justify-end space-x-4">
            <Link href="/generation-history">
              <Button variant="outline">Cancel</Button>
            </Link>
            <Button 
              onClick={handleSaveChanges}
              className="bg-gradient-to-r from-blue-600 to-purple-600 hover:from-blue-700 hover:to-purple-700"
            >
              Save Changes
            </Button>
          </div>
        </CardContent>
      </Card>
    </div>
  )
}